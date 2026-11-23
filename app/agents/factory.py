"""Builds the Luna orchestrator for the app. Nothing is contacted until Luna is on and used."""

from app.agents.luna.agent import LunaAgent
from app.agents.orchestrator import AgentOrchestrator
from app.repositories.base import Repository
from app.services.llm import LoggedLlm, make_llm_client


def make_agent_orchestrator(repo: Repository) -> AgentOrchestrator:
    """The client is created only when a run first needs it, from the .env settings."""
    return AgentOrchestrator(repo, lambda: LunaAgent(LoggedLlm(make_llm_client())))
