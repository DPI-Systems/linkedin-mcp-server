from fastapi import Request

from app.services.linkedin import LinkedInClient
from app.workers.runner import JobRunner


def get_runner(request: Request) -> JobRunner:
    return request.app.state.runner


def get_client(request: Request) -> LinkedInClient:
    return request.app.state.client_factory()
