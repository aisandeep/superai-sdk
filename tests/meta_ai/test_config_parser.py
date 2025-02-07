import os.path

import pytest
import yaml
from pydantic import ValidationError

from superai.meta_ai.config_parser import AIConfig


@pytest.fixture
def config_file():
    yield os.path.join(os.path.dirname(__file__), "fixtures", "test_config.yaml")


@pytest.fixture
def conf(config_file):
    with open(config_file, "r") as conf_file_stream:
        conf: dict = yaml.safe_load(conf_file_stream)
        yield conf


def test_parsed_config(config_file: str):
    config = AIConfig(_env_file=config_file)
    assert config.dict()
    assert config.template
    assert config.instance
    assert config.deploy
    assert config.template.name == "MyKerasModel_template"


def test_root_validator(conf: dict, tmp_path):
    conf.pop("deploy", None)
    with open(tmp_path.joinpath("new_file.yaml"), "w") as tf_file:
        yaml.dump(data=conf, stream=tf_file, allow_unicode=True, default_flow_style=False)
        with pytest.raises(ValueError) as e:
            loaded_config = AIConfig(_env_file=str(tmp_path.joinpath("new_file.yaml")))


def test_check_orchestrator(conf: dict, tmp_path):
    conf["deploy"]["orchestrator"] = "SOMETHING_RANDOM"
    with open(tmp_path.joinpath("new_file.yaml"), "w") as tf_file:
        yaml.dump(data=conf, stream=tf_file, allow_unicode=True, default_flow_style=False)
        with pytest.raises(ValidationError) as e:
            loaded_config = AIConfig(_env_file=str(tmp_path.joinpath("new_file.yaml")))


def test_most_basic_settings(tmp_path):
    config = {
        "template": {"name": "template_name", "description": "some description", "model_class": "some_class"},
        "instance": {"name": "instance_name"},
        "deploy": {"orchestrator": "AWS_EKS"},
    }
    with open(tmp_path.joinpath("config.yaml"), "w") as tf_file:
        yaml.dump(data=config, stream=tf_file, allow_unicode=True, default_flow_style=False)
        loaded_config = AIConfig(_env_file=str(tmp_path.joinpath("config.yaml")))
    assert loaded_config.template.name == config["template"]["name"]
    assert loaded_config.template.description == config["template"]["description"]
    assert loaded_config.instance.name == config["instance"]["name"]
    assert loaded_config.deploy.orchestrator == config["deploy"]["orchestrator"]
