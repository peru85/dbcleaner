#!/usr/bin/env python3
import yaml
import mysql.connector
from mysql.connector import Error
import argparse
import subprocess
import datetime
import os
import logging
import time
import gzip
import shutil
from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("maintenance.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def load_config(file_path: str) -> dict:
    logger.info("Loading configuration from %s", file_path)
    with open(file_path, "r") as f:
        config = yaml.safe_load(f)
    return config


def run_sql(cursor, connection, sql_command: str, dry_run: bool):
    """
    Execute an SQL command and commit.
    In dry-run mode, only print the command.
    This function also consumes any extra result sets.
    """
    logger.info("Executing SQL: %s", sql_command)
    if dry_run:
        logger.info("[DRY RUN] Would execute SQL: %s", sql_command)
        return None
    else:
        cursor.execute(sql_command)
        while cursor.nextset():
            pass
        connection.commit()
        if cursor.with_rows:
            result = cursor.fetchall()
            return result
        return None


def check_foreign_keys(cursor, db_name: str, table_name: str):
    query = f"""
    SELECT CONSTRAINT_NAME, TABLE_NAME, COLUMN_NAME,
           REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
    FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
    WHERE TABLE_SCHEMA = '{db_name}'
      AND TABLE_NAME = '{table_name}'
      AND REFERENCED_TABLE_NAME IS NOT NULL;
    """
    logger.info("Checking foreign keys for table `%s`.`%s`", db_name, table_name)
    cursor.execute(query)
    result = cursor.fetchall()
    while cursor.nextset():
        pass
    return result


def dump_table(db_name: str, table_name: str, conn_params: dict, table_config: dict, dry_run: bool):
    """
    Create a compressed dump of the specified table.
    The dump file is named as: <db>_<table>_<timestamp>.sql.gz.
    For local storage, the dump is saved to the specified dump_path.
    For s3 storage, the dump file is uploaded via the S3Uploader class.
    """
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    file_name = f"{db_name}_{table_name}_{timestamp}.sql.gz"

    dump_storage = table_config.get("dump_storage", "local").lower()
    dump_path = table_config.get("dump_path", ".")
    os.makedirs(dump_path, exist_ok=True)
    dump_file = os.path.join(dump_path, file_name)

    host = conn_params['host']
    user = conn_params['user']
    password = conn_params['password']
    mysqldump_cmd = conn_params.get("mysqldump_path", "mysqldump")
    dump_args = [mysqldump_cmd, '-h', host, '-u', user, f'-p{password}', db_name, table_name]
    masked_dump = ' '.join(dump_args).replace(f'-p{password}', '-p****')


    cmd = (
        f"{mysqldump_cmd} -h {conn_params['host']} -u {conn_params['user']} "
        f"-p{conn_params['password']} {db_name} {table_name} | gzip > {dump_file}"
    )
    masked_cmd = cmd.replace(f"-p{conn_params['password']}", "-p****")

    bash_cmd = (
        f"set -o pipefail; {mysqldump_cmd} -h {host} -u {user} -p{password} "
        f"{db_name} {table_name} | gzip > {dump_file}"
    )
    masked_bash_cmd = bash_cmd.replace(f"-p{password}", "-p****")

    if dry_run:
        logger.info("[DRY RUN] Would execute dump command: %s", masked_dump)
    else:
        logger.info("Dumping table `%s`.`%s` to %s", db_name, table_name, dump_file)
        try:
            proc = subprocess.Popen(dump_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            with gzip.open(dump_file, 'wb') as f_out:
                shutil.copyfileobj(proc.stdout, f_out)
            stderr = proc.stderr.read().decode('utf-8', errors='ignore')
            retcode = proc.wait()
            if retcode != 0:
                logger.error("Error dumping table `%s`: %s", table_name, stderr)
                if os.path.exists(dump_file):
                    os.remove(dump_file)
                return None
            logger.info("Dump compressed to %s", dump_file)
        except Exception as e:
            logger.error("Error during streaming dump for `%s`: %s", table_name, e)
            if os.path.exists(dump_file):
                os.remove(dump_file)
            return None

    if dump_storage == "s3" and dry_run :
        logger.info("[DRY RUN] Would execute S3 upload to: %s", os.getenv("AWS_BUCKET"))
    elif dump_storage == "s3" and not dry_run:
        try:
            from s3_uploader import S3Uploader  # Separate module for S3 uploads
            uploader = S3Uploader()
            s3_key = f"db_dumps/{file_name}"
            uploader.upload_file(dump_file, s3_key)
            logger.info("Uploaded %s to S3 as %s", dump_file, s3_key)
            os.remove(dump_file)
        except Exception as e:
            logger.error("Error uploading %s to S3: %s", dump_file, e)
    return dump_file


def process_table(cursor, connection, db_name: str, table: dict, conn_params: dict, results: list, dry_run: bool):
    table_name = table.get("name")
    msg = f"Processing table `{db_name}`.`{table_name}`"
    results.append(msg)
    logger.info(msg)


    if table.get("dump_before", False):
        success = dump_table(db_name, table_name, conn_params, table, dry_run)
        if success is None:
            # failsafe: stop further processing on dump failure
            msg = f"Skipping `{table_name}` due to dump failure."
            results.append(msg)
            logger.error(msg)
            return

    if table.get("check_foreign_keys", False):
        try:
            fk_info = check_foreign_keys(cursor, db_name, table_name)
            if fk_info:
                msg = f"Foreign keys found for `{table_name}`: {fk_info}"
                results.append(msg)
                logger.info(msg)
            else:
                msg = f"No foreign keys found for `{table_name}`."
                results.append(msg)
                logger.info(msg)
        except Error as e:
            msg = f"Error checking foreign keys for `{table_name}`: {e}"
            results.append(msg)
            logger.error(msg)

    batch_size = table.get("delete_batch_size")
    batch_delay = table.get("delete_batch_delay", 0)

    delete_strategy = table.get("delete_strategy", None)
    if delete_strategy:
        if delete_strategy.lower() == "truncate":
            try:
                sql = f"TRUNCATE TABLE `{table_name}`;"
                run_sql(cursor, connection, sql, dry_run)
                msg = f"Table `{table_name}` truncated successfully."
                results.append(msg)
                logger.info(msg)
            except Error as e:
                msg = f"Error truncating table `{table_name}`: {e}"
                results.append(msg)
                logger.error(msg)

        elif delete_strategy.lower() == "condition":
            cond = table.get("delete_condition")
            if not cond:
                msg = f"No delete_condition for {table_name}";
                results.append(msg);
                logger.warning(msg)
            else:
                batch = table.get("delete_batch_size")
                delay = table.get("delete_batch_delay", 0)
                if batch:
                    logger.info("Condition delete in batches of %s, delay %s", batch, delay)
                    while True:
                        sql = f"DELETE FROM `{table_name}` WHERE {cond} LIMIT {batch};"
                        run_sql(cursor, connection, sql, dry_run)
                        affected = cursor.rowcount if not dry_run else 0
                        msg = f"Batch deleted {affected} rows from {table_name}";
                        results.append(msg);
                        logger.info(msg)
                        if affected < batch: break
                        if delay and not dry_run: time.sleep(delay)
                else:
                    sql = f"DELETE FROM `{table_name}` WHERE {cond};"
                    run_sql(cursor, connection, sql, dry_run)
                    affected = cursor.rowcount if not dry_run else 0
                    msg = f"Deleted {affected} rows from {table_name} using condition";
                    results.append(msg);
                    logger.info(msg)

        elif delete_strategy == "older_than_days":
            days = table.get("delete_older_than_days")
            date_col = table.get("date_column", "date")
            if days is None:
                logger.warning("No delete_older_than_days specified for %s; skipping.", table_name)
            else:
                thresh = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
                condition = f"{date_col} < '{thresh}'"

                batch_size = table.get("delete_batch_size")
                batch_delay = table.get("delete_batch_delay", 0)

                if batch_size:
                    logger.info("Deleting in batches of %s with %ss delay", batch_size, batch_delay)
                    while True:
                        sql = f"DELETE FROM `{table_name}` WHERE {condition} LIMIT {batch_size};"
                        run_sql(cursor, connection, sql, dry_run)
                        affected = cursor.rowcount if not dry_run else 0
                        logger.info("Batch deleted %s rows from %s", affected, table_name)
                        results.append(f"Batch deleted {affected} rows from `{table_name}`")
                        if affected < batch_size:
                            break
                        if batch_delay and not dry_run:
                            time.sleep(batch_delay)
                else:
                    # single delete
                    sql = f"DELETE FROM `{table_name}` WHERE {condition};"
                    run_sql(cursor, connection, sql, dry_run)
                    affected = cursor.rowcount if not dry_run else 0
                    msg = f"Deleted {affected} rows older than {days} days from `{table_name}`"
                    results.append(msg)
                    logger.info(msg)

    if table.get("run_optimize", False):
        try:
            sql = f"OPTIMIZE TABLE `{table_name}`;"
            optimize_result = run_sql(cursor, connection, sql, dry_run)
            msg = f"Optimized `{table_name}`: {optimize_result}"
            results.append(msg)
            logger.info(msg)
        except Error as e:
            msg = f"Error optimizing table `{table_name}`: {e}"
            results.append(msg)
            logger.error(msg)


def main():
    parser = argparse.ArgumentParser(description="MySQL Maintenance Script")
    parser.add_argument("--dry-run", action="store_true",
                        help="Only print the SQL and dump commands without executing them")
    parser.add_argument("--config", default="maintenance_config.yml", help="Path to YAML configuration file")
    args = parser.parse_args()
    dry_run = args.dry_run
    config_file = args.config

    config = load_config(config_file)
    load_dotenv()

    conn_params = {
        "host":  os.getenv("DB_HOST"),
        "user": os.getenv("DB_USERNAME"),
        "password": os.getenv("DB_PASSWORD"),
        "port": os.getenv("DB_PORT", 3306),
        "mysqldump_path": "mysqldump"  # Adjust if mysqldump is not in your PATH
    }

    connection = None
    try:
        connection = mysql.connector.connect(
            host=conn_params["host"],
            user=conn_params["user"],
            password=conn_params["password"],
            port=conn_params["port"]
        )
        if connection.is_connected():
            logger.info("Successfully connected to MySQL")
            results = []
            cursor = connection.cursor(buffered=True)
            for db in config.get("databases", []):
                db_name = db.get("name")
                try:
                    cursor.execute(f"USE `{db_name}`;")
                    msg = f"Using database `{db_name}`"
                    results.append(msg)
                    logger.info(msg)
                except Error as e:
                    msg = f"Error selecting database `{db_name}`: {e}"
                    results.append(msg)
                    logger.error(msg)
                    continue

                for table in db.get("tables", []):
                    process_table(cursor, connection, db_name, table, conn_params, results, dry_run)

            logger.info("Maintenance Results:")
            for res in results:
                logger.info(res)

            cursor.close()
    except Error as e:
        logger.error("Error connecting to MySQL: %s", e)
    finally:
        if connection.is_connected():
            connection.close()
            logger.info("MySQL connection closed")


if __name__ == "__main__":
    main()
