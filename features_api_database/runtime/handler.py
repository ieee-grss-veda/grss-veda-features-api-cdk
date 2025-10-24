"""
Custom resource lambda handler to bootstrap Postgres db.
Source: https://github.com/developmentseed/eoAPI/blob/master/deployment/handlers/db_handler.py
"""
import json

import boto3
import psycopg
import requests
from psycopg import sql
from psycopg.conninfo import make_conninfo


def send(
    event,
    context,
    responseStatus,
    responseData,
    physicalResourceId=None,
    noEcho=False,
):
    """
    Copyright 2016 Amazon Web Services, Inc. or its affiliates. All Rights Reserved.
    This file is licensed to you under the AWS Customer Agreement (the "License").
    You may not use this file except in compliance with the License.
    A copy of the License is located at http://aws.amazon.com/agreement/ .
    This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, express or implied.
    See the License for the specific language governing permissions and limitations under the License.

    Send response from AWS Lambda.

    Note: The cfnresponse module is available only when you use the ZipFile property to write your source code.
    It isn't available for source code that's stored in Amazon S3 buckets.
    For code in buckets, you must write your own functions to send responses.
    """
    responseUrl = event["ResponseURL"]

    print(responseUrl)

    responseBody = {}
    responseBody["Status"] = responseStatus
    responseBody["Reason"] = (
        "See the details in CloudWatch Log Stream: " + context.log_stream_name
    )
    responseBody["PhysicalResourceId"] = physicalResourceId or context.log_stream_name
    responseBody["StackId"] = event["StackId"]
    responseBody["RequestId"] = event["RequestId"]
    responseBody["LogicalResourceId"] = event["LogicalResourceId"]
    responseBody["NoEcho"] = noEcho
    responseBody["Data"] = responseData

    json_responseBody = json.dumps(responseBody)

    print("Response body:\n" + json_responseBody)

    headers = {"content-type": "", "content-length": str(len(json_responseBody))}

    try:
        response = requests.put(responseUrl, data=json_responseBody, headers=headers)
        print("Status code: " + response.reason)
    except Exception as e:
        print("send(..) failed executing requests.put(..): " + str(e))


def get_secret(secret_name):
    """Get Secrets from secret manager."""
    print(f"Fetching {secret_name}")
    client = boto3.client(service_name="secretsmanager")
    response = client.get_secret_value(SecretId=secret_name)
    return json.loads(response["SecretString"])


def create_db(cursor, db_name: str) -> None:
    """Create DB."""
    print(f"DEBUG: create_db called with db_name='{db_name}'")

    try:
        cursor.execute("SELECT datname FROM pg_catalog.pg_database ORDER BY datname")
        existing_dbs = [row[0] for row in cursor.fetchall()]

        print(f"DEBUG: Existing databases: {existing_dbs}")

        if db_name in existing_dbs:
            print(f"DEBUG: Database '{db_name}' already exists")
        else:
            print(f"DEBUG: Database '{db_name}' not found, creating...")
            cursor.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name))
            )
            print(f"DEBUG: Database '{db_name}' created successfully")

            cursor.execute("SELECT datname FROM pg_catalog.pg_database WHERE datname = %s", [db_name])
            if cursor.fetchone():
                print(f"DEBUG: Database '{db_name}' exists after creation")
            else:
                print(f"DEBUG: Database '{db_name}' was NOT created successfully")

    except Exception as e:
        print(f"ERROR: create_db failed: {e}")
        raise


def create_user(cursor, username: str, password: str) -> None:
    """Create User."""

    print(f"DEBUG: create_user called with username='{username}'")

    try:
        # Check if user exists before
        cursor.execute("SELECT rolname FROM pg_roles WHERE rolname = %s", (username,))
        exists_before = cursor.fetchone() is not None
        print(f"DEBUG: User '{username}' exists before: {exists_before}")

        # Create/update user
        cursor.execute(
            sql.SQL(
                "DO $$ "
                "BEGIN "
                "  IF NOT EXISTS ( "
                "       SELECT 1 FROM pg_roles "
                "       WHERE rolname = {user}) "
                "  THEN "
                "    CREATE USER {username} "
                "    WITH PASSWORD {password}; "
                "  ELSE "
                "    ALTER USER {username} "
                "    WITH PASSWORD {password}; "
                "  END IF; "
                "END "
                "$$; "
            ).format(username=sql.Literal(username), password=sql.Literal(password), user=sql.Literal(username))
        )
        print(f"DEBUG: SQL executed successfully")

        # Check if user exists after
        cursor.execute("SELECT rolname FROM pg_roles WHERE rolname = %s", (username,))
        exists_after = cursor.fetchone() is not None
        print(f"DEBUG: User '{username}' exists after: {exists_after}")

        if exists_after:
            print(f"DEBUG: User '{username}' created/updated successfully")
        else:
            print(f"DEBUG: User '{username}' was NOT created")

    except Exception as e:
        print(f"ERROR: create_user failed: {e}")
        raise


def create_permissions(cursor, db_name: str, username: str) -> None:
    """Add permissions and user-specific pgstac configuration."""
    cursor.execute(
        sql.SQL(
            "GRANT CONNECT ON DATABASE {db_name} TO {username};"
            "GRANT CREATE ON DATABASE {db_name} TO {username};"  # Allow schema creation
            "GRANT USAGE ON SCHEMA public TO {username};"
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "GRANT ALL PRIVILEGES ON TABLES TO {username};"
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "GRANT ALL PRIVILEGES ON SEQUENCES TO {username};"
        ).format(
            db_name=sql.Identifier(db_name),
            username=sql.Identifier(username),
        )
    )

def register_extensions(cursor) -> None:
    """Add PostGIS extension."""
    cursor.execute(sql.SQL("CREATE EXTENSION IF NOT EXISTS postgis;"))


def add_SRID_9311(cursor) -> None:
    """Add 9311 SRID to spatial_ref_sys"""

    cursor.execute(sql.SQL(
        "INSERT INTO spatial_ref_sys (srid,auth_name,auth_srid,srtext,proj4text) VALUES (9311,'EPSG',9311,{srtext},{proj4text}) ON CONFLICT (srid) DO NOTHING;"
        ).format(
            srtext="PROJCS['NAD27 / US National Atlas Equal Area',GEOGCS['NAD27',DATUM['North_American_Datum_1927',SPHEROID['Clarke 1866',6378206.4,294.978698213898],EXTENSION['PROJ4_GRIDS','NTv2_0.gsb']],PRIMEM['Greenwich',0,AUTHORITY['EPSG','8901']],UNIT['degree',0.0174532925199433,AUTHORITY['EPSG','9122']],AUTHORITY['EPSG','4267']],PROJECTION['Lambert_Azimuthal_Equal_Area'],PARAMETER['latitude_of_center',45],PARAMETER['longitude_of_center',-100],PARAMETER['false_easting',0],PARAMETER['false_northing',0],UNIT['metre',1,AUTHORITY['EPSG','9001']],AXIS['Easting',EAST],AXIS['Northing',NORTH],AUTHORITY['EPSG','9311']]",
            proj4text="+proj=laea +R_A +lat_0=45 +lon_0=-100 +x_0=0 +y_0=0 +ellps=clrk66 +nadgrids=NTv2_0.gsb +units=m +no_defs +type=crs"
        )
    )


def handler(event, context):
    """Lambda Handler."""
    print(f"Handling {event}")

    if event["RequestType"] not in ["Create", "Update"]:
        return send(event, context, "SUCCESS", {"msg": "No action to be taken"})

    try:
        params = event["ResourceProperties"]
        connection_params = get_secret(params["conn_secret_arn"])
        user_params = get_secret(params["new_user_secret_arn"])

        print("Connecting to admin DB...")
        print(f"DEBUG: admin dbname")
        admin_db_conninfo = make_conninfo(
            dbname=connection_params.get("dbname", "postgres"),
            user=connection_params["username"],
            password=connection_params["password"],
            host=connection_params["host"],
            port=connection_params["port"],
        )
        with psycopg.connect(admin_db_conninfo, autocommit=True) as conn:
            with conn.cursor() as cur:
                print("Creating database...")
                create_db(
                    cursor=cur,
                    db_name=user_params["dbname"],
                )

                print("Creating user...")
                create_user(
                    cursor=cur,
                    username=user_params["username"],
                    password=user_params["password"],
                )

                print("Setting permissions...")
                create_permissions(
                    cursor=cur,
                    db_name=user_params["dbname"],
                    username=user_params["username"],
                )

        features_db_conninfo = make_conninfo(
            dbname=user_params["dbname"],
            user=connection_params["username"],
            password=connection_params["password"],
            host=connection_params["host"],
            port=connection_params["port"],
        )
        with psycopg.connect(features_db_conninfo, autocommit=True) as conn:
            with conn.cursor() as cur:
                print("Registering PostGIS ...")
                register_extensions(cursor=cur)

        with psycopg.connect(features_db_conninfo, autocommit=True) as conn:
            with conn.cursor() as cur:
                print("Adding SRID 9311 ...")
                add_SRID_9311(cursor=cur)

    except Exception as e:
        print(f"Unable to bootstrap database with exception={e}")
        return send(event, context, "FAILED", {"message": str(e)})

    print("Complete.")
    return send(event, context, "SUCCESS", {})
