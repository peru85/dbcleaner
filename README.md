# MySQL database cleanup script
This script was written to keep a large database junk-free and consolidate several other small bash cleanup scripts into one. 

## Capabilities
 - can do --dry-run to only show what queries would be ran
 - has a logger so we can keep an audit trail as to what happened
 - can dump before deletion and store to local path or upload to s3
 - can define deletion strategy
 - has .env loader
 - can define a yml model with all the configuration, see example

## How to use
define a model.yml. See the example file for reference.
use the .env.template to create your own .env with your own config. It is by default filled to connect to the local testing mysql.

### as a local script

```bash
python3 dbcleaner.py -h
```
usage: dbcleaner.py [-h] [--dry-run] [--config CONFIG]

MySQL Maintenance Script

options:
-h, --help       show this help message and exit
--dry-run        Only print the SQL and dump commands without executing them
--config CONFIG  Path to YAML configuration file

### as a docker container
1. build the container
>docker build -t dbcleaner:latest .
3. run the container with args
>docker run --rm maintenance:latest --dry-run --config model.yml
note: the default model.yml was burned in the container image so we can test it easily

## Testing
I have included a test mysql as a docker-compose.yml with a test sql schema (courtesy of https://filldb.info/ , a very cool little project).

Run:
>docker compose up -d
Check your browser for phpMyAdmin on:
http://127.0.0.1:8080/

then run
>docker run  --network host --rm maintenance:latest --dry-run --config model.yml

remove --dry-run if you want to test it live. NOTE: the "--network host" is needed so the container can connect to localhost.


If you want to use your custom model you can mount it like this:
>docker run   -v D:\OneDrive\PythonProjects\dbCleaner\model2.yml:/app/model2.yml --rm maintenance:latest --dry-run --config model2.yml
