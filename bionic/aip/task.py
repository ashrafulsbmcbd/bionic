"""
Data model for task running on AI platform
"""
import logging
import pickle
import time
from typing import Callable, Optional

import attr

from bionic.aip.state import State, AipError
from bionic.deps.optdep import import_optional_dependency


@attr.s(auto_attribs=True, frozen=True)
class TaskConfig:
    """
    Contains configuration that can differ per task, which can be a
    single machine or a cluster of machines.

    In the future could be extended to support GPUs and similar.
    """

    machine: str
    worker_count: Optional[int] = None
    worker_machine: Optional[str] = None


@attr.s(auto_attribs=True, frozen=True)
class Config:
    """
    Contains configuration that remains the same across all tasks.

    Attributes
    ----------
    uuid: str
        The globally unique job name on AI platform
    image_uri: str
        The full address on gcr of the docker image for the tasks
    project: str
        The GCP project where the jobs will be run
    """

    uuid: str
    image_uri: str
    project_name: str
    account: Optional[str] = None
    network: Optional[str] = None


@attr.s(auto_attribs=True, frozen=True)
class Task:
    # This task object will be serialized and sent to AIP. Hence, all entities
    # here must be serializable as well.
    name: str
    function: Callable
    config: Config
    task_config: TaskConfig

    @property
    def job_id(self) -> str:
        return f"{self.config.uuid}_{self.name}"

    @property
    def inputs_uri(self) -> str:
        # In a future version it might be better to make this path
        # specified by the flow, so that it can be inside a GCS cache
        # location and different file types.
        return f"gs://{self.config.project_name}/bionic/{self.config.uuid}/{self.name}-inputs.cloudpickle"

    @property
    def output_uri(self) -> str:
        # In a future version it might be better to make this path
        # specified by the flow, so that it can be inside a GCS cache
        # location and different file types.
        return f"gs://{self.config.project_name}/bionic/{self.config.uuid}/{self.name}-output.cloudpickle"

    def _ai_platform_job_spec(self):
        """Conversion from our task data model to a job request on ai platform"""
        output = {
            "jobId": f"{self.job_id}",
            "labels": {"job": self.config.uuid},
            "trainingInput": {
                "serviceAccount": self.config.account,
                "masterType": self.task_config.machine,
                "masterConfig": {"imageUri": self.config.image_uri},
                "args": ["python", "-m", "bionic.aip.main", self.inputs_uri],
                "packageUris": [],
                "region": "us-west1",
                "pythonModule": "",
                "scaleTier": "CUSTOM",
            },
        }
        if self.config.network is not None:
            output["trainingInput"]["network"] = self.config.network

        if (
            self.task_config.worker_count is not None
            and self.task_config.worker_count > 0
        ):
            output["trainingInput"]["workerCount"] = self.task_config.worker_count
            output["trainingInput"]["workerType"] = self.task_config.worker_machine
            output["trainingInput"]["workerConfig"] = {
                "imageUri": self.config.image_uri
            }

        return output

    def _stage(self, gcs_fs):
        cloudpickle = import_optional_dependency("cloudpickle")

        path = self.inputs_uri
        logging.info(f"Staging task {self.name} at {path}")

        with gcs_fs.open(path, "wb") as f:
            cloudpickle.dump(self, f)

    def submit(self, gcs_fs, aip_client):
        self._stage(gcs_fs)
        spec = self._ai_platform_job_spec()

        logging.info(f"Submitting {self.config.project_name}: {self}")

        request = (
            aip_client.projects()
            .jobs()
            .create(body=spec, parent=f"projects/{self.config.project_name}")
        )
        request.execute()
        url = f"https://console.cloud.google.com/ai-platform/jobs/{self.job_id}"
        logging.info(f"Started task on AI Platform: {url}")

    def wait_for_results(self, gcs_fs, aip_client):
        state, error = self._get_state_and_error(aip_client)
        while state.is_executing():
            time.sleep(10)
            state, error = self._get_state_and_error(aip_client)
            logging.info(f"Future for {self.job_id} has state {state}")

        if state is not State.SUCCEEDED:
            raise AipError(f"{self.job_id}: " + str(error))

        with gcs_fs.open(self.output_uri, "rb") as f:
            return pickle.load(f)

    def _get_state_and_error(self, aip_client):
        request = (
            aip_client.projects()
            .jobs()
            .get(name=f"projects/{self.config.project_name}/jobs/{self.job_id}")
        )
        response = request.execute()
        state, error = State[response["state"]], response.get("errorMessage", None)
        return state, error
