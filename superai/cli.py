import json
import os
import pathlib
import shutil
import signal
import sys
from datetime import datetime
from typing import List, Optional
from urllib.parse import urlparse

import click
import yaml
from botocore.exceptions import ClientError
from pycognito import Cognito
from requests import ReadTimeout
from rich import print
from rich.console import Console

from superai import __version__
from superai.apis.meta_ai.model import PredictionError
from superai.client import Client
from superai.config import get_config_dir, list_env_configs, set_env_config, settings
from superai.exceptions import SuperAIAuthorizationError
from superai.log import logger
from superai.utils import (
    load_api_key,
    remove_aws_credentials,
    save_api_key,
    save_aws_credentials,
    save_cognito_user,
)
from superai.utils.files import download_file_to_directory
from superai.utils.pip_config import pip_configure

BASE_FOLDER = get_config_dir()
COGNITO_USERPOOL_ID = settings.get("cognito", {}).get("userpool_id")
COGNITO_CLIENT_ID = settings.get("cognito", {}).get("client_id")
COGNITO_REGION = settings.get("cognito", {}).get("region")

log = logger.get_logger(__name__)


def _signal_handler(s, f):
    sys.exit(1)


@click.group()
def cli():
    pass


@cli.command()
@click.option("--verbose/--no-verbose", "-vvv", help="Verbose output", default=False)
def info(verbose):
    """Print CLI Configuration"""
    click.echo("=================")
    click.echo("Super.AI CLI Info:")
    click.echo("=================")
    load_api_key()
    click.echo(f"VERSION: {__version__}")
    click.echo(f"ENVIRONMENT: {settings.current_env}")
    click.echo(f"USER: {settings.get('user',{}).get('username')}")
    if verbose:
        click.echo(yaml.dump(settings.as_dict(env=settings.current_env), default_flow_style=False))


@cli.group()
@click.pass_context
def env(ctx):
    """
    super.AI Config operations
    """


@env.command(name="list")
@click.pass_context
def env_list(ctx):
    """

    :param ctx:
    :return:
    """
    list_env_configs(printInConsole=True)


@env.command(name="set")
@click.option("--api-key", help="Your super.AI API KEY", required=False)
@click.option("--environment", "-e", help="Set environment", required=False)
@click.pass_context
def env_set(ctx, api_key, environment):
    """
    Set configuration
    """
    if environment:
        set_env_config(name=environment)
    if api_key:
        save_api_key(api_key)


@cli.group()
@click.pass_context
def client(ctx):
    """
    super.AI API operations
    """
    api_key = ""
    try:
        api_key = load_api_key()
    except Exception:
        pass
    if len(api_key) == 0:
        print("User needs to login or set api key")
        exit()
    ctx.obj = {}
    ctx.obj["client"] = Client(
        api_key=api_key, auth_token=settings.get("user", {}).get("cognito", {}).get("access_token")
    )


# Create decorator to pass client object when needed
pass_client = click.make_pass_decorator(Client, ensure=True)


@client.command(name="create_jobs")
@click.option("--app_id", "-a", help="Application id", required=True)
@click.option("--callback_url", "-c", help="Callback URL for post when jobs finish")
@click.option("--inputs", "-i", help="Json list with inputs")
@click.option("--inputs_file", "-if", help="URL pointing to JSON file")
@click.pass_context
def create_jobs(ctx, app_id: str, callback_url: str, inputs: str, inputs_file: str):
    """
    Submit jobs
    """
    client = ctx.obj["client"]
    print("Submitting jobs")
    json_inputs = None
    if inputs is not None:
        try:
            json_inputs = json.loads(inputs)
        except:
            print("Couldn't read json inputs")
            exit()
    print(client.create_jobs(app_id, callback_url, json_inputs, inputs_file))


@client.command(name="fetch_job")
@click.option("--job_id", "-j", help="Job id", required=True)
@click.pass_context
def fetch_job(ctx, job_id: str):
    """
    Get Job given job id
    """
    client = ctx.obj["client"]
    print(f"Fetching job {job_id}")
    print(client.fetch_job(job_id))


@client.command(name="fetch_batches_job")
@click.option("--app_id", "-a", help="App id", required=True)
@click.pass_context
def fetch_batches_job(ctx, app_id: str):
    """
    Get not processed Batches given app id
    """
    client = ctx.obj["client"]
    print(f"Fetching batches {app_id}")
    print(client.fetch_batches_job(app_id))


@client.command(name="fetch_batch_job")
@click.option("--app_id", "-a", help="App id", required=True)
@click.option("--batch_id", "-b", help="Batch id", required=True)
@click.pass_context
def fetch_batch_job(ctx, app_id: str, batch_id: str):
    """
    Get Batch given app id and batch id
    """
    client = ctx.obj["client"]
    print(f"Fetching batch {app_id} {batch_id}")
    print(client.fetch_batch_job(app_id, batch_id))


@client.command(name="get_job_response")
@click.option("--job_id", "-j", help="Job id", required=True)
@click.pass_context
def get_job_response(ctx, job_id: str):
    """
    Get Job response given job id
    """
    client = ctx.obj["client"]
    print(f"Getting job response {job_id}")
    print(client.get_job_response(job_id))


@client.command(name="cancel_job")
@click.option("--job_id", "-j", help="Job id", required=True)
@click.pass_context
def cancel_job(ctx, job_id: str):
    """
    Cancel a job given job id. Only for jobs in SCHEDULED, IN_PROGRESS or SUSPENDED state.
    """
    client = ctx.obj["client"]
    print(f"Cancelling job {job_id}")
    print(client.cancel_job(job_id))


@client.command(name="list_jobs")
@click.option("--app_id", "-a", help="Application id", required=True)
@click.option("--page", "-p", help="Page number", type=int)
@click.option("--size", "-s", help="Size of page", type=int)
@click.option("--sort_by", "-sort", help="Job field to sort by", type=str, default="id", show_default=True)
@click.option(
    "--order_by",
    "-order",
    help="Sort direction (asc or desc)",
    type=click.Choice(["asc", "desc"]),
    default="asc",
    show_default=True,
)
@click.option(
    "--created_start_date",
    "-c0",
    help="Created start date",
    type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]),
)
@click.option(
    "--created_end_date",
    "-c1",
    help="Created end date",
    type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]),
)
@click.option(
    "--completed_start_date",
    "-e0",
    help="Completed start date",
    type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]),
)
@click.option(
    "--completed_end_date",
    "-e1",
    help="Completed end date",
    type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]),
)
@click.option(
    "--status_in",
    "-s_in",
    help="Status of jobs",
    multiple=True,
    type=click.Choice(["SCHEDULED", "IN_PROGRESS", "FAILED", "SUSPENDED", "CANCELED", "EXPIRED", "COMPLETED"]),
)
@click.pass_context
def list_jobs(
    ctx,
    app_id: str,
    page: int,
    size: int,
    sort_by: str,
    order_by: str,
    created_start_date: datetime,
    created_end_date: datetime,
    completed_start_date: datetime,
    completed_end_date: datetime,
    status_in: List[str] = None,
):
    """
    Get a paginated list of jobs (without response) given an application id
    """
    client = ctx.obj["client"]
    print(f"Fetching jobs per application {app_id}")
    if len(status_in) == 0:
        status_in = None
    print(
        client.list_jobs(
            app_id,
            page,
            size,
            sort_by,
            order_by,
            created_start_date,
            created_end_date,
            completed_start_date,
            completed_end_date,
            status_in,
        )
    )


@client.command(name="download_jobs")
@click.option("--app_id", "-a", help="Application id", required=True)
@click.option(
    "--created_start_date",
    "-c0",
    help="Created start date",
    type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]),
)
@click.option(
    "--created_end_date",
    "-c1",
    help="Created end date",
    type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]),
)
@click.option(
    "--completed_start_date",
    "-e0",
    help="Completed start date",
    type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]),
)
@click.option(
    "--completed_end_date",
    "-e1",
    help="Completed end date",
    type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]),
)
@click.option(
    "--send_email/--not_send_email",
    "-se/-nse",
    help="Choose if email is sent at the end",
    default=True,
)
@click.option(
    "--status_in",
    "-s_in",
    help="Status of jobs",
    multiple=True,
    type=click.Choice(["SCHEDULED", "IN_PROGRESS", "FAILED", "SUSPENDED", "CANCELED", "EXPIRED", "COMPLETED"]),
)
@click.option(
    "--with_history/--not_with_history",
    "-wh/-nwh",
    help="Choose if add job history to downloaded data",
    default=False,
)
@click.pass_context
def download_jobs(
    ctx,
    app_id: str,
    created_start_date: datetime,
    created_end_date: datetime,
    completed_start_date: datetime,
    completed_end_date: datetime,
    send_email: bool = None,
    status_in: List[str] = None,
    with_history: bool = None,
):
    """
    Trigger processing of job responses that is sent to customer email (default) once is finished.
    """
    client = ctx.obj["client"]
    print(f"Triggering job responses processing per application {app_id}")
    if len(status_in) == 0:
        status_in = None
    print(
        client.download_jobs(
            app_id,
            created_start_date,
            created_end_date,
            completed_start_date,
            completed_end_date,
            status_in,
            send_email,
            with_history,
        )
    )


@client.command(name="get_jobs_operation")
@click.option("--app_id", "-a", help="Application id", required=True)
@click.option("--operation_id", "-o", help="Operation id", required=True)
@click.pass_context
def get_jobs_operation(
    ctx,
    app_id: str,
    operation_id: str,
):
    """
    Fetch jobs operation given application id and operation id
    """
    client = ctx.obj["client"]
    print(f"Fetching jobs operation per application {app_id} operation {operation_id}")
    print(client.get_jobs_operation(app_id, operation_id))


@client.command(name="downloaded_jobs_url")
@click.option("--app_id", "-a", help="Application id", required=True)
@click.option("--operation_id", "-o", help="Operation id", required=True)
@click.option("--seconds_ttl", "-sttl", help="Seconds ttl for url", default=60, type=click.INT, show_default=True)
@click.pass_context
def downloaded_jobs_url(
    ctx,
    app_id: str,
    operation_id: str,
    seconds_ttl: int,
):
    """
    Generates downloaded jobs url given application id and operation id
    """
    client = ctx.obj["client"]
    print(f"Generating downloaded jobs url per application {app_id} operation {operation_id}")
    print(client.generates_downloaded_jobs_url(app_id, operation_id, seconds_ttl))


@client.command(name="download_tasks")
@click.option("--app_id", "-a", help="Application id", required=True)
@click.option(
    "--created_start_date",
    "-c0",
    help="Created start date",
    type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]),
)
@click.option(
    "--created_end_date",
    "-c1",
    help="Created end date",
    type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]),
)
@click.option(
    "--completed_start_date",
    "-e0",
    help="Completed start date",
    type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]),
)
@click.option(
    "--completed_end_date",
    "-e1",
    help="Completed end date",
    type=click.DateTime(formats=["%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"]),
)
@click.option(
    "--status_in",
    "-s_in",
    help="Status of tasks",
    multiple=True,
    type=click.Choice(["SCHEDULED", "IN_PROGRESS", "FAILED", "CANCELED", "PENDING", "EXPIRED", "COMPLETED"]),
)
@click.pass_context
def download_tasks(
    ctx,
    app_id: str,
    created_start_date: datetime,
    created_end_date: datetime,
    completed_start_date: datetime,
    completed_end_date: datetime,
    status_in: List[str] = None,
):
    """
    Trigger download of tasks data that can be retrieved using task operation id.
    """
    client = ctx.obj["client"]
    print(f"Triggering task download processing per application {app_id}")
    if len(status_in) == 0:
        status_in = None
    print(
        client.download_tasks(
            app_id,
            created_start_date,
            created_end_date,
            completed_start_date,
            completed_end_date,
            status_in,
        )
    )


@client.command(name="get_tasks_operation")
@click.option("--app_id", "-a", help="Application id", required=True)
@click.option("--operation_id", "-o", help="Operation id", required=True)
@click.pass_context
def get_tasks_operation(
    ctx,
    app_id: str,
    operation_id: str,
):
    """
    Fetch tasks operation given application id and operation id
    """
    client = ctx.obj["client"]
    print(f"Fetching tasks operation per application {app_id} operation {operation_id}")
    print(client.get_tasks_operation(app_id, operation_id))


@client.command(name="downloaded_tasks_url")
@click.option("--app_id", "-a", help="Application id", required=True)
@click.option("--operation_id", "-o", help="Operation id", required=True)
@click.option("--seconds_ttl", "-sttl", help="Seconds ttl for url", default=60, type=click.INT, show_default=True)
@click.pass_context
def downloaded_tasks_url(
    ctx,
    app_id: str,
    operation_id: str,
    seconds_ttl: int,
):
    """
    Generates downloaded tasks url given application id and operation id
    """
    client = ctx.obj["client"]
    print(f"Generating downloaded tasks url per application {app_id} operation {operation_id}")
    print(client.generates_downloaded_tasks_url(app_id, operation_id, seconds_ttl))


@client.command(name="create_ground_truth")
@click.option("--app_id", "-a", help="Application id", required=True)
@click.option("--input_json", "-i", help="Input json of ground truth", required=True)
@click.option("--label", "-l", help="Label (or output) json of ground truth", required=True)
@click.option("--tag", "-t", help="Tag ground truth data")
@click.option("--metadata", "-m", help="Metadata json")
@click.pass_context
def create_ground_truth(
    ctx, app_id: str, input_json: str = None, label: str = None, tag: str = None, metadata: str = None
):
    """
    Submit fresh ground truth data
    """
    client = ctx.obj["client"]
    print("Submitting fresh ground truth data")
    input_dict = None
    metadata_dict = None
    label_dict = None
    if input_json is not None:
        try:
            input_dict = json.loads(input_json)
        except:
            print("Couldn't load input json of ground truth")
            exit()
    if metadata is not None:
        try:
            metadata_dict = json.loads(metadata)
        except:
            print("Couldn't load metadata json of ground truth")
            exit()
    if label is not None:
        try:
            label_dict = json.loads(label)
        except:
            print("Couldn't load label json of ground truth")
            exit()
    print(client.create_ground_truth(app_id, input_dict, label_dict, tag, metadata_dict))


@client.command(name="update_ground_truth")
@click.option("--ground_truth_data_id", "-g", help="Ground truth data id", required=True)
@click.option("--input_json", "-i", help="Input json of ground truth")
@click.option("--label", "-l", help="Label (or output) json of ground truth")
@click.option("--tag", "-t", help="Tag ground truth data")
@click.option("--metadata", "-m", help="Metadata json")
@click.pass_context
def update_ground_truth(
    ctx, ground_truth_data_id: str, input_json: str = None, label: str = None, tag: str = None, metadata: str = None
):
    """
    Update (patch) ground truth data
    """
    client = ctx.obj["client"]
    print(f"Updating ground truth data {ground_truth_data_id}")
    input_dict = None
    metadata_dict = None
    label_dict = None
    if input_json is not None:
        try:
            input_dict = json.loads(input_json)
        except:
            print("Couldn't load input json of ground truth")
            exit()
    if metadata is not None:
        try:
            metadata_dict = json.loads(metadata)
        except:
            print("Couldn't load metadata json of ground truth")
            exit()
    if label is not None:
        try:
            label_dict = json.loads(label)
        except:
            print("Couldn't load label json of ground truth")
            exit()
    print(client.update_ground_truth(ground_truth_data_id, input_dict, label_dict, tag, metadata_dict))


@client.command(name="list_ground_truth_data")
@click.option("--app_id", "-a", help="Application id", required=True)
@click.option("--page", "-p", help="Page number", type=int)
@click.option("--size", "-s", help="Size of page", type=int)
@click.pass_context
def list_ground_truth_data(ctx, app_id: str, page: int, size: int):
    """
    List all ground truth data for an application
    """
    client = ctx.obj["client"]
    print(f"Fetching ground truth data per application {app_id}")
    print(client.list_ground_truth_data(app_id, page, size))


@client.command(name="get_ground_truth_data")
@click.option("--ground_truth_data_id", "-g", help="Ground truth data id", required=True)
@click.pass_context
def get_ground_truth_data(ctx, ground_truth_data_id: str):
    """
    Fetch single ground truth data object
    """
    client = ctx.obj["client"]
    print(f"Fetching ground truth data {ground_truth_data_id}")
    print(client.get_ground_truth_data(ground_truth_data_id))


@client.command(name="delete_ground_truth_data")
@click.option("--ground_truth_data_id", "-g", help="Ground truth data id", required=True)
@click.pass_context
def delete_ground_truth_data(ctx, ground_truth_data_id: str):
    """
    Mark ground truth data as deleted
    """
    client = ctx.obj["client"]
    print(f"Deleting ground truth data {ground_truth_data_id}")
    print(client.delete_ground_truth_data(ground_truth_data_id))


@client.command(name="create_ground_truth_from_job")
@click.option("--app_id", "-a", help="Application id", required=True)
@click.option("-job_id", "-j", help="Job id", required=True)
@click.pass_context
def create_ground_truth_from_job(ctx, app_id: str, job_id: str):
    client = ctx.obj["client"]
    print(f"Converting job {job_id} to ground truth data")
    print(client.create_ground_truth_from_job(app_id, job_id))


@client.command(name="workflow_delete")
@click.option(
    "dp_qualified_name", "-d", help="The name of the rounter, for example image_disambiguation.router", required=True
)
@click.option("workflow_name", "-w", help="The name of the workflow", required=True)
@click.pass_context
def delete_workflow(ctx, dp_qualified_name, workflow_name):
    """
    Delete an existing workflow
    """
    client = ctx.obj["client"]
    new_workflows = client.delete_workflow(dp_qualified_name, workflow_name)
    logger.info(
        f"Workflow {workflow_name} deleted with success from {dp_qualified_name}, new workflow list -> {new_workflows}"
    )


@cli.command()
@click.option("--api-key", help="Your super.AI API KEY", required=True)
def config(api_key):
    """
    Set api key.
    """
    save_api_key(api_key)


@cli.command()
@click.option("--username", "-u", help="super.AI Username", required=True)
@click.option("--password", "-p", prompt=True, hide_input=True)
@click.option(
    "--show-pip/--no-show-pip",
    "-pip",
    default=False,
    help="Shows how to set pip configuration manually",
    show_default=True,
)
def login(username, password, show_pip):
    """
    Use username and password to get super.AI api key.
    """
    user = Cognito(
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        user_pool_id=COGNITO_USERPOOL_ID,
        client_id=COGNITO_CLIENT_ID,
        user_pool_region=COGNITO_REGION,
        username=username,
    )
    try:
        user.authenticate(password)
    except ClientError as e:
        if (
            e.response["Error"]["Code"] == "UserNotFoundException"
            or e.response["Error"]["Code"] == "NotAuthorizedException"
        ):
            print("Incorrect username or password")
            return
        else:
            print(f"Unexpected error: {e}")
            return

    client = Client(auth_token=user.access_token, id_token=user.id_token)
    api_keys = client.get_apikeys()
    if len(api_keys) > 0:
        save_api_key(api_keys[0], username=username)
        save_cognito_user(user)
        print(f"Api key {api_keys[0]} was set")
    else:
        print(f"User {username} doesn't have any api keys")

    try:
        aws_credentials = client.get_awskeys()
        if aws_credentials:
            save_aws_credentials(aws_credentials)
            pip_configure(show_pip=show_pip)
    except SuperAIAuthorizationError as authorization_error:
        logger.debug(f"ERROR Authorization: {str(authorization_error)}")
        remove_aws_credentials()
    except Exception as exception:
        logger.debug(f"ERROR: {str(exception)}")
        remove_aws_credentials()


@cli.command()
def logout():
    """
    Remove stored api key
    """
    save_api_key("")
    print("Stored api key was removed")


@cli.group()
def ai():
    """View, list and control models and their deployments."""


@ai.command("list")
@click.option("--name", required=False, help="Filter by model name.")
@click.option("--version", required=False, help="Filter by model version.")
@pass_client
def list_ai(client, name: str, version: str):
    """List available models"""
    if name is None:
        print(client.get_all_models())
    elif version is None:
        print(client.get_model_by_name(str(name)))
    else:
        print(client.get_model_by_name_version(str(name), str(version)))


@ai.command("view")
@click.argument("id", type=click.UUID)
@pass_client
def get_ai(client, id: str):
    """View model parameters"""
    print(client.get_model(str(id)))


@ai.command("update")
@click.argument("id", type=click.UUID)
@click.option("--name", required=False, help="Model name")
@click.option("--description", required=False, help="Model description")
@click.option(
    "--visibility",
    required=False,
    type=click.Choice(["PRIVATE", "PUBLIC"]),
    help="Model visibility",
    show_choices=True,
)
@pass_client
def update_ai(client, id: str, name: str, description: str, visibility: str):
    """Update model parameters"""
    params = {}
    if name is not None:
        params["name"] = name
    if description is not None:
        params["description"] = description
    if visibility is not None:
        params["visibility"] = visibility

    print(client.update_model(str(id), **params))


@ai.command("download")
@click.argument("artifact_type", type=click.Choice(["source", "weights"]))
@click.argument("id", type=click.UUID)
@click.option("--app-id", required=False, help="Application id, necessary for app trained models.")
@click.option(
    "--path",
    required=False,
    help="Path to download artifact. Default is current working directory.",
    type=click.Path(exists=True, writable=True),
    default=os.getcwd(),
)
@pass_client
def download_artifact(client, id: str, artifact_type: str, app_id: str, path: str):
    """Download model artifact"""
    # Use rich progress to wait for url
    console = Console()
    with console.status(f"Preparing download of {artifact_type} in backend...") as status:
        url = client.get_artifact_download_url(model_id=str(id), artifact_type=artifact_type, app_id=app_id)

    parsed = urlparse(url)
    url_path = pathlib.Path(parsed.path)
    filename = url_path.name

    logger.info(f"Downloading {filename} to {path}")
    with console.status("Downloading...") as status:
        download_file_to_directory(url=url, filename=filename, path=path)


@ai.group()
def method():
    """Directly call the AI methods to train and predict"""


@method.command("train", help="Start training of an AI object")
@click.option(
    "--path",
    "-p",
    default=".",
    help="Path to AI object save location. A new AI template and instance will be created from this path. Ensure this "
    "is the absolute path",
    required=True,
    type=click.Path(exists=True, readable=True, dir_okay=True),
)
@click.option(
    "--model-save-path",
    "-mp",
    help="Path to location where the weights will be saved.",
    required=True,
    show_default=True,
    type=click.Path(),
)
@click.option(
    "--training-data-path",
    "-tp",
    help="Path to location where the training data is stored in the local file system.",
    required=True,
    type=click.Path(exists=False, readable=False),
)
@click.option(
    "--test-data-path",
    "-tsp",
    help="Path to location where the test data is stored in the local file system.",
    type=click.Path(exists=False, readable=False),
)
@click.option(
    "--validation-data-path",
    "-vp",
    help="Path to location where the validation data is stored in the local file system.",
    type=click.Path(exists=False, readable=False),
)
@click.option(
    "--production-data-path",
    "-pp",
    help="Path to location where the production data is stored in the local file system.",
    type=click.Path(exists=False, readable=False),
)
@click.option(
    "--weights-path",
    "-wp",
    help="Path to location where the existing weights is stored local file system.",
    type=click.Path(exists=False, readable=False),
)
@click.option("--encoder-trainable/--no-encoder-trainable", default=False, show_default=True, type=bool)
@click.option("--decoder-trainable/--no-decoder-trainable", default=False, show_default=True, type=bool)
@click.option(
    "--hyperparameters",
    "-h",
    multiple=True,
    help="Hyperparameters to be passed. Please pass them as `-h train_split=0.2 -h cross_valid=False`",
)
@click.option(
    "--model-parameters",
    "-m",
    multiple=True,
    help="Model parameters to be passed. Please pass them as `-m some_parameter=0.2 -h other_parameter=False -h "
    "listed=['list','of','strings']`",
)
@click.option(
    "--callbacks",
    "-cl",
    help="Callbacks to be passed to training (yet to be implemented)",
)
@click.option(
    "--train-logger",
    "-tl",
    multiple=True,
    help="Train logger (yet to be implemented)",
)
def train(
    path,
    model_save_path,
    training_data_path,
    test_data_path,
    validation_data_path,
    production_data_path,
    weights_path,
    encoder_trainable,
    decoder_trainable,
    hyperparameters,
    model_parameters,
    callbacks,
    train_logger,
):
    from superai.meta_ai.ai import AI
    from superai.meta_ai.parameters import HyperParameterSpec, ModelParameters

    click.echo(
        f"Starting training from the path {path} with hyperparameters {hyperparameters} "
        f"and model parameters {model_parameters}"
    )
    processed_hyperparameters = HyperParameterSpec.load_from_list(hyperparameters)
    processed_model_parameters = ModelParameters.load_from_list(model_parameters)
    ai_object = AI.load_local(path)
    ai_object.train(
        model_save_path=model_save_path,
        training_data=training_data_path,
        test_data=test_data_path,
        production_data=production_data_path,
        validation_data=validation_data_path,
        weights_path=weights_path,
        encoder_trainable=encoder_trainable,
        decoder_trainable=decoder_trainable,
        hyperparameters=processed_hyperparameters,
        model_parameters=processed_model_parameters,
        callbacks=callbacks,
        train_logger=train_logger,
    )


@method.command("predict", help="Predict from AI")
@click.option(
    "--path",
    "-p",
    default=".",
    help="Path to AI object save location. A new AI template and instance will be created from this path",
    required=True,
    type=click.Path(exists=True, readable=True),
)
@click.option(
    "--data-path",
    "-dp",
    help="Path to file location where the input data is stored in the local file system.",
    type=click.Path(exists=True, readable=True, dir_okay=False),
    required=False,
)
@click.option("--json-input", "-i", required=False, type=str, help="Prediction input. Should be a valid JSON string")
@click.option(
    "--weights-path",
    "-wp",
    required=False,
    help="Path to weights to be loaded",
    type=click.Path(exists=True, readable=True),
)
@click.option(
    "--metrics-output-dir",
    required=False,
    help="If provided, metrics will be computed and saved to this directory.",
    type=click.Path(file_okay=False, path_type=pathlib.Path),
)
def predict(path, json_input=None, data_path: str = None, weights_path=None, metrics_output_dir=None):
    from superai.meta_ai.ai_helper import load_and_predict

    result = load_and_predict(
        model_path=path,
        weights_path=weights_path,
        data_path=data_path,
        json_input=json_input,
        metrics_output_dir=metrics_output_dir,
    )
    click.echo(f"Result : {result}")


@ai.group(help="Deployed models running in our infrastructure")
def deployment():
    pass


@deployment.command("list")
@pass_client
@click.option(
    "--model_id",
    type=str,
    default=None,
    help="Filter deployments by model id. If not provided, all deployments will be listed",
)
@click.option(
    "--status",
    type=str,
    default=None,
    help="Filter deployments by status. "
    "If not provided, all deployments will be listed. "
    "Must be one of the values "
    '"FAILED", "MAINTENANCE", "OFFLINE", "ONLINE", "PAUSED", "STARTING", "UNKNOWN"',
)
def list_deployments(client, model_id: Optional[str] = None, status: Optional[str] = None):
    """List all deployments"""
    d = client.list_deployments(model_id=model_id, status=status)
    for deployment in d:
        print(f"[b][u]Model: {deployment.model.name}[/b][/u]")
        print(f"{deployment}\n")


@deployment.command("view")
@click.argument("id", type=click.UUID)
@pass_client
def view_deployment(client, id: str):
    """View deployment parameters"""
    print(client.get_deployment(str(id)))


@deployment.command("start")
@click.argument("id", type=click.UUID)
@click.option(
    "--wait",
    type=click.INT,
    default=0,
    help="Allow command to block and wait for deployment to be ready. Returns when deployment is ONLINE.",
    show_default=True,
)
@pass_client
def start_deployment(client, id: str, wait: int):
    """Create a deployment for the model."""
    print("Starting deployment...")
    if wait:
        print(f"Waiting for up to {wait} seconds. Note: Some deployments can take up to 15 minutes (900 seconds).")
    reached_state = client.set_deployment_status(str(id), target_status="ONLINE", timeout=wait)
    if reached_state:
        print("Deployment online.")
    else:
        print("Stopped waiting for ONLINE status. Process is still running in the backend.")


@deployment.command("stop")
@click.argument("id", type=click.UUID)
@click.option(
    "--wait",
    type=click.INT,
    default=0,
    help="Allow command to block and wait for deployment to be ready. Returns when deployment is ONLINE.",
    show_default=True,
)
@pass_client
def stop_deployment(client, id: str, wait: int):
    """Stop and tear-down a model deployment."""
    print("Tearing down model deployment...")
    if wait:
        print(f"Waiting for up to {wait} seconds.")
    reached_state = client.set_deployment_status(str(id), target_status="OFFLINE", timeout=wait)
    if reached_state:
        print("Deployment offline.")
    else:
        print("Stopped waiting for OFFLINE status. Process is still running in the backend.")


@deployment.command("predict")
@click.argument("id", type=click.UUID)
@click.argument(
    "data",
    type=str,
)
@click.option(
    "--parameters",
    type=str,
    help="Parameters to be used for prediction. Expected as JSON encoded dictionary.",
)
@click.option(
    "--timeout",
    type=int,
    help="Time to wait for prediction to complete. Expect worst case timeouts of 900 seconds (15 minutes) for new "
    "deployment startups.",
    default=20,
    show_default=True,
)
@pass_client
def predict(client, id: str, data: str, parameters: str, timeout: int):
    """
    Predict using a deployed model

    `DATA` is the input to be used for prediction. Expected as JSON encoded dictionary.

    """
    try:
        response = client.predict_from_endpoint(
            model_id=str(id),
            input_data=json.loads(data),
            parameters=json.loads(parameters) if parameters else None,
            timeout=timeout,
        )
        print(response)
    except ReadTimeout:
        print("Timeout waiting for prediction to complete. Try increasing --timeout value.")
    except PredictionError:
        # TODO: Print error message when available in object
        print("Prediction failed. Check the logs for more information.")


@ai.group()
def prediction():
    """View and list predictions"""


@prediction.command("view")
@click.argument("id", required=True, type=click.UUID)
@pass_client
def view_prediction(client, id):
    """View prediction object"""
    p = client.get_prediction_with_data(str(id))
    print(p.__json_data__)


@deployment.command(
    "scaling",
    help="Control scaling of deployed models. Currently only supports configuring the automatic scale-in of models "
    "after a period of no prediction activity.",
)
@click.argument("id", type=click.UUID)
@click.option(
    "--min_instances",
    type=click.INT,
    required=False,
    default=0,
    help="Minimum number of instances allowed.",
    show_default=True,
)
@click.option(
    "--scale_in_timeout",
    type=click.INT,
    required=False,
    default=None,
    help="Allow scale-in after this number of seconds without prediction. Should be higher than the time it takes to "
    "startup a model.",
    show_default=True,
)
@pass_client
def scaling(client, id: str, min_instances: int, scale_in_timeout: int):
    current = client.get_deployment(str(id))
    print(
        f"Current settings:"
        f"\n\tmin_instances: {current['min_instances']}"
        f"\n\tscale_in_timeout: {current['scale_in_timeout']}"
    )
    if min_instances is not None:
        print("Changing config: min_instances")
        print(client.set_min_instances(str(id), min_instances))
    if scale_in_timeout is not None:
        print("Changing config: scale_in_timeout")
        print(client.set_scale_in_timeout(str(id), scale_in_timeout))


@ai.group()
def docker():
    """Docker specific commands"""


@docker.command(name="build", help="Build a docker image for a sagemaker model.")
@click.option("--image-name", "-i", required=True, help="Name of the image to be built")
@click.option(
    "--entry-point",
    "-e",
    required=True,
    help="Path to file which will serve as entrypoint to the sagemaker model. Generally this is a method which calls "
    "the predict method",
)
@click.option("--dockerfile", "-d", help="Path to Dockerfile.", default="Dockerfile", show_default=True)
@click.option(
    "--command", "-c", help="Command to run after the entrypoint in the image.", default="serve", show_default=True
)
@click.option("--worker-count", "-w", help="Number of workers to run.", default=1, show_default=True)
@click.option(
    "--entry-point-method",
    "-em",
    help="Method to be called inside the entry point. Make sure this method accepts the input data and context. ",
    default="handle",
    show_default=True,
)
@click.option(
    "--use-shell", "-u", help="Use shell to run the build process, which is more verbose. Used by default", default=True
)
def build_docker_image(image_name, entry_point, dockerfile, command, worker_count, entry_point_method, use_shell):
    from superai.meta_ai.dockerizer import build_image

    build_image(
        image_name=image_name,
        entry_point=entry_point,
        dockerfile=dockerfile,
        command=command,
        worker_count=worker_count,
        entry_point_method=entry_point_method,
        use_shell=use_shell,
    )


@docker.command(name="push")
@click.argument("id", required=True, type=click.UUID)
@click.option(
    "--image-name", "-i", required=True, help="Name of the image to be pushed. You can get this from `docker image ls`"
)
@click.option("--region", "-r", help="AWS region", default="us-east-1", show_default=True)
def push_docker_image(model_id, image_name, region):
    """Push the docker image built by `superai model docker-build` to ECR.

    ID is the UUID of the AI model.
    Check `superai ai list` to see the list of models with their UUIDs.
    """
    from superai.meta_ai.dockerizer import push_image

    if ":" in image_name:
        image, version = image_name.split(":")
    else:
        image = image_name
        version = None
    push_image(image_name=image, model_id=str(model_id), version=version, region=region)


@docker.command(
    "run-local",
    help="Run a docker container built by `superai model docker-build` locally. "
    "We assume here that the ports 8080 & 8081 are available",
)
@click.option("--image-name", "-i", required=True, help="Name of the image to be run")
@click.option(
    "--model-path",
    "-m",
    required=True,
    help="Path to the folder containing weights file to be used for getting inference",
    type=click.Path(exists=True, readable=True),
)
@click.option(
    "--gpu",
    "-g",
    default=False,
    help="Run docker with GPUs enabled. Make sure this is a GPU container with cuda enabled, "
    "and nvidia-container-runtime installed",
    show_default=True,
)
def docker_run_local(image_name, model_path, gpu):
    options = [f"-v {os.path.abspath(model_path)}:/opt/ml/model/", "-p 80:8080", "-p 8081:8081 "]
    if gpu:
        options.append("--rm --gpus all")
    options = " ".join(options)
    command = f"docker run {options} {image_name}"
    logger.info(f"Running command: {command}")
    os.system(command)


@docker.command(
    "invoke-local",
    help="Invoke the locally deployed container. The API description of the local container can be found at "
    "http://localhost/api-description",
)
@click.option(
    "--mime",
    "-mm",
    default="application/json",
    help="MIME type of the payload. `application/json` will be sent to the invocation directly. For other MIME types, "
    "you can pass the path to file with --body. If its a valid path, it will be loaded and sent to the request. "
    "Default: `application/json`",
)
@click.option(
    "--body", "-b", required=True, help="Body of payload to be sent to the invocation. Can be a path to a file as well."
)
def docker_invoke_local(mime, body):
    from superai.meta_ai.dockerizer.sagemaker_endpoint import invoke_local

    invoke_local(mime, body)


@ai.group()
def training():
    """View and manage trainings"""


@training.command(name="list")
@click.option("--app_id", "-a", help="Application id", required=False)
@click.option("--model_id", "-m", help="Model id", required=False)
@click.option(
    "--state",
    "-s",
    help="Filter by state",
    required=False,
    default=None,
    show_default=True,
    type=click.Choice(["FINISHED", "IN_PROGRESS", "FAILED", "PAUSED", "UNKNOWN"]),
)
@click.option(
    "--limit", "-l", help="Limit the number of returned rows", required=False, default=10, show_default=True, type=int
)
@pass_client
def list_trainings(client, app_id, model_id, state, limit):
    """
    List trainings. Allows filtering by state and application id.
    """
    trainings = client.get_trainings(app_id, model_id, state=state, limit=limit)
    if trainings:
        print(trainings)


@training.command(name="start")
@click.option("--app_id", "-a", help="Application id", required=True)
@click.option("--model_id", "-m", help="Model id", required=True)
@click.option(
    "--properties",
    "-p",
    help="Custom properties, if not set default ones for the template will be used",
    required=True,
)
@pass_client
def start_training(client, app_id, model_id, properties: str):
    """
    Start a new training
    """
    json_inputs = None
    if properties:
        try:
            json_inputs = json.loads(properties)
        except:
            print("Couldn't read json inputs")
            exit()

    id = client.create_training_entry(model_id, app_id, json_inputs)
    if id:
        print(f"Started a new training with ID {id}")


@training.command(name="trigger-template")
@click.option("--app_id", "-a", help="Application id", required=True)
@click.option("--model_id", "-m", help="Model id", required=True)
@click.option("--training_template_id", "-tt", help="Training Template id", required=True)
@click.option("--task-name", "-tn", help="Task name to prepare dataset", required=True, type=str)
@click.option("--properties", "-p", help="Custom properties", required=False, type=dict)
@click.option("--metadata", "-md", help="Metadata", required=False, type=dict)
@pass_client
def trigger_template_training(client, app_id, model_id, training_template_id, task_name, properties, metadata):
    try:
        if properties:
            properties = json.loads(properties)
        if metadata:
            metadata = json.loads(metadata)
    except:
        print("Could process JSON properties or metadata")
        exit()

    id = client.start_training_from_app_model_template(
        app_id=app_id,
        model_id=model_id,
        task_name=task_name,
        training_template_id=training_template_id,
        current_properties=properties,
        metadata=metadata,
    )
    if id:
        print(f"Started new training with ID {id}")


@training.command("deploy", help="Deploy a AI from its config file")
@click.option(
    "--config-file",
    "-c",
    help="Config YAML file containing AI properties and training deployment definition",
    type=click.Path(exists=True, readable=True, dir_okay=True, path_type=pathlib.Path),
)
@click.option("--push/--no-push", "-p/-np", help="Push the model before training", default=True)
@click.option(
    "--clean/--no-clean", "-cl/-ncl", help="Remove the local .AISave folder to perform a fresh deployment", default=True
)
def training_deploy(config_file, push=True, clean=True):
    from superai.meta_ai.ai_helper import obtain_object_template_config

    if clean:
        if os.path.exists(".AISave"):
            shutil.rmtree(".AISave")
    ai_object, ai_template_object, config_data = obtain_object_template_config(config_file)

    if config_data.training_deploy is not None:
        if push:
            ai_object.push(
                update_weights=config_data.training_deploy.update_weights,
                overwrite=config_data.training_deploy.overwrite,
            )
        ai_object.training_deploy(
            orchestrator=config_data.training_deploy.orchestrator,
            training_data_dir=config_data.training_deploy.training_data_dir,
            skip_build=config_data.training_deploy.skip_build,
            properties=config_data.training_deploy.properties,
            training_parameters=config_data.training_deploy.training_parameters,
            enable_cuda=config_data.training_deploy.enable_cuda,
            build_all_layers=config_data.training_deploy.build_all_layers,
            envs=config_data.training_deploy.envs,
            download_base=config_data.training_deploy.download_base,
        )
    elif config_data.training_deploy_from_app is not None:
        if push:
            ai_object.push(
                update_weights=config_data.training_deploy_from_app.update_weights,
                overwrite=config_data.training_deploy_from_app.overwrite,
            )
        ai_object.start_training_from_app(
            app_id=config_data.training_deploy_from_app.app_id,
            task_name=config_data.training_deploy_from_app.task_name,
            current_properties=config_data.training_deploy_from_app.current_properties,
            metadata=config_data.training_deploy_from_app.metadata,
            skip_build=config_data.training_deploy_from_app.skip_build,
            enable_cuda=config_data.training_deploy_from_app.enable_cuda,
            build_all_layers=config_data.training_deploy_from_app.build_all_layers,
            envs=config_data.training_deploy_from_app.envs,
            download_base=config_data.training_deploy_from_app.download_base,
        )
    else:
        raise ValueError("configuration should contain 'training_deploy' or 'training_deploy_from_app' section")


@training.group()
def template():
    """View and manage training templates"""


@template.command(name="create")
@click.option("--app_id", "-a", help="Application id", required=False, default=None)
@click.option("--model_id", "-m", help="Root Model id or Model id", required=True)
@click.option(
    "--properties",
    "-p",
    help="Training parameters passed to the model when starting training.",
    required=True,
)
@pass_client
def create_template(client, app_id, model_id, properties: str):
    """
    Create a template for trainings.
    The template is used to instantiate new training instances.
    """
    json_inputs = None
    if properties:
        try:
            json_inputs = json.loads(properties)
        except:
            print("Couldn't read json inputs")
            exit()

    id = client.create_training_template_entry(model_id=model_id, properties=json_inputs, app_id=app_id)
    if id:
        print(f"Created new training with ID {id}")


@template.command(name="update")
@click.option("--app_id", "-a", help="Application id", required=False, default=None)
@click.option("--model_id", "-m", help="Model id", required=True)
@click.option(
    "--properties",
    "-p",
    help="Training parameters passed to the model when starting training.",
    required=False,
)
@click.option(
    "--description",
    "-d",
    help="Description of the template",
    required=False,
)
@pass_client
def update_template(client, app_id, model_id, properties: str, description: str):
    """
    Update an exising template for trainings.
    The template is used to instantiate new training instances.
    """
    if properties:
        try:
            json_inputs = json.loads(properties)
        except:
            print("Couldn't read json inputs")
            exit()
    else:
        json_inputs = None
    id = client.update_training_template(
        model_id=model_id, app_id=app_id, properties=json_inputs, description=description
    )
    if id:
        print(f"Updated training template with id={id}")


@template.command(name="list")
@click.option("--app_id", "-a", help="Application id", required=False, default=None)
@click.option("--model_id", "-m", help="Model id", required=True)
@pass_client
def list_training_templates(client, app_id, model_id):
    """
    List existing training templates.
    """
    templates = client.get_training_templates(model_id, app_id)
    if templates:
        print(templates)


@template.command(name="view")
@click.option("--app_id", "-a", help="Application id", required=False, default=None)
@click.option("--template_id", "-t", help="Template id", required=True)
@pass_client
def view_training_template(client, app_id, template_id):
    """
    List existing training templates.
    """
    template = client.get_training_template(template_id, app_id)
    if template:
        print(template)


@ai.command("deploy", help="Deploy a AI from its config file")
@click.option(
    "--config-file",
    "-c",
    help="Config YAML file containing AI properties and deployment definition",
    type=click.Path(exists=True, readable=True, dir_okay=True, path_type=pathlib.Path),
)
@click.option(
    "--clean/--no-clean", "-cl/-ncl", help="Remove the local .AISave folder to perform a fresh deployment", default=True
)
@click.option("--push/--no-push", "-p/-np", help="Push to create a model entry", default=False)
def deploy_ai(config_file, clean=True, push=False):
    from superai.meta_ai.ai_helper import obtain_object_template_config
    from superai.meta_ai.deployed_predictors import DeployedPredictor

    if clean:
        if os.path.exists(".AISave"):
            shutil.rmtree(".AISave")
    ai_object, ai_template_object, config_data = obtain_object_template_config(config_file=config_file)
    print(f"Configuration : {config_data}")
    if isinstance(config_data.deploy.properties, str):
        properties = json.loads(config_data.deploy.properties)
    else:
        properties = config_data.deploy.properties
    if push or config_data.deploy.push:
        ai_object.push(
            update_weights=config_data.deploy.update_weights,
            overwrite=config_data.deploy.overwrite,
            weights_path=config_data.instance.weights_path,
        )
    predictor: DeployedPredictor = ai_object.deploy(
        orchestrator=config_data.deploy.orchestrator,
        skip_build=config_data.deploy.skip_build,
        properties=properties,
        enable_cuda=config_data.deploy.enable_cuda,
        enable_eia=config_data.deploy.enable_eia,
        cuda_devel=config_data.deploy.cuda_devel,
        redeploy=config_data.deploy.redeploy,
        build_all_layers=config_data.deploy.build_all_layers,
        download_base=config_data.deploy.download_base,
        envs=config_data.deploy.envs,
        worker_count=config_data.deploy.worker_count,
        ai_cache=config_data.deploy.ai_cache,
    )
    predictor_dictionary = {predictor.__class__.__name__: predictor.to_dict()}
    with open(
        os.path.join(settings.path_for(), "cache", ai_object.name, str(ai_object.version), ".predictor_config.json"),
        "w",
    ) as predictor_config:
        logger.info("Storing predictor config")
        json.dump(predictor_dictionary, predictor_config)


@ai.command("predictor-test", help="Test the predictor created from the deploy command")
@click.option(
    "--config-file",
    "-c",
    help="Points to the config file",
    type=click.Path(exists=True, readable=True, dir_okay=True, path_type=pathlib.Path),
)
@click.option("--predict-input", "-i", help="Prediction input", type=str, required=False)
@click.option("--predict-input-file", "-if", help="Prediction input file", type=click.Path(), required=False)
@click.option(
    "--expected-output",
    "-o",
    help="Expected output (should only be used if your model is consistent)",
    type=str,
    required=False,
)
@click.option(
    "--expected-output-file",
    "-of",
    help="Expected output file (should only be used if your model is consistent",
    type=click.Path(),
)
@pass_client
def predictor_test(
    client, config_file, predict_input=None, predict_input_file=None, expected_output=None, expected_output_file=None
):
    from superai.meta_ai.ai_helper import obtain_object_template_config
    from superai.meta_ai.deployed_predictors import DeployedPredictor

    ai_object, ai_template_object, config_data = obtain_object_template_config(config_file=config_file)
    config_path = os.path.join(
        settings.path_for(), "cache", ai_object.name, str(ai_object.version), ".predictor_config.json"
    )
    if os.path.exists(config_path):
        with open(config_path, "r") as predictor_config:
            predictor_dictionary = json.load(predictor_config)
        predictor: DeployedPredictor = DeployedPredictor.from_dict(predictor_dictionary, client)
        if predict_input is not None:
            predict_input = json.loads(predict_input)
        elif predict_input_file is not None:
            with open(predict_input_file, "r") as predict_file_stream:
                predict_input = json.load(predict_file_stream)
        else:
            raise ValueError("One of --predict-input or --predict-input-file should be passed")
        predicted_output = predictor.predict(predict_input)
        click.echo(predicted_output)
        if expected_output is not None:
            assert predicted_output == expected_output, "Expected output should be same as predicted output"
        if expected_output_file is not None:
            with open(expected_output_file, "r") as output_file_stream:
                expected_output = json.load(output_file_stream)
                assert predicted_output == expected_output, "Expected output should be same as predicted output"
    else:
        raise Exception(f"Predictor config does not exist at {config_path}")


@ai.command("predictor-teardown", help="Teardown the predictor in context")
@click.option(
    "--config-file",
    "-c",
    help="Points to the config file",
    type=click.Path(exists=True, readable=True, dir_okay=True, path_type=pathlib.Path),
)
@pass_client
def predictor_teardown(client, config_file):
    from superai.meta_ai.ai_helper import obtain_object_template_config
    from superai.meta_ai.deployed_predictors import DeployedPredictor

    ai_object, ai_template_object, config_data = obtain_object_template_config(config_file=config_file)
    config_path = os.path.join(
        settings.path_for(), "cache", ai_object.name, str(ai_object.version), ".predictor_config.json"
    )
    if os.path.exists(config_path):
        with open(config_path, "r") as predictor_config:
            predictor_dictionary = json.load(predictor_config)
        predictor: DeployedPredictor = DeployedPredictor.from_dict(predictor_dictionary, client)
        predictor.terminate()
        logger.info(f"Removing predictor config at {config_path}")
        os.remove(config_path)
    else:
        raise Exception(f"Predictor config did not exist at {config_path}")


def main():
    signal.signal(signal.SIGINT, _signal_handler)
    sys.exit(cli())


if __name__ == "__main__":
    main()
