from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

PDL_ENRICH_URL = "https://api.peopledatalabs.com/v5/person/enrich"
DEFAULT_MIN_LIKELIHOOD = 4


@dataclass(frozen=True)
class PdlPersonQuery:
    first_name: str
    last_name: str
    school: str | None = None
    company: str | None = None
    location: str | None = None

    def as_params(self, min_likelihood: int) -> dict[str, object]:
        params: dict[str, object] = {
            "first_name": self.first_name,
            "last_name": self.last_name,
            "min_likelihood": min_likelihood,
        }
        if self.school:
            params["school"] = self.school
        if self.company:
            params["company"] = self.company
        if self.location:
            params["location"] = self.location
        return params


@dataclass(frozen=True)
class PdlPersonResult:
    status_code: int
    likelihood: int | None
    data: dict[str, Any] | None
    error: str | None
    raw_body: bytes | None


class PdlClient:
    def __init__(
        self,
        api_key: str,
        min_likelihood: int = DEFAULT_MIN_LIKELIHOOD,
        timeout: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.min_likelihood = min_likelihood
        self.timeout = timeout

    async def enrich_person(
        self,
        query: PdlPersonQuery,
        client: httpx.AsyncClient | None = None,
    ) -> PdlPersonResult:
        if client is not None:
            return await self._enrich_person(query, client)
        async with httpx.AsyncClient(timeout=self.timeout) as owned_client:
            return await self._enrich_person(query, owned_client)

    async def _enrich_person(
        self,
        query: PdlPersonQuery,
        client: httpx.AsyncClient,
    ) -> PdlPersonResult:
        response = await client.get(
            PDL_ENRICH_URL,
            params=query.as_params(self.min_likelihood),
            headers={"X-Api-Key": self.api_key},
            timeout=self.timeout,
        )
        if response.status_code == 429:
            await asyncio.sleep(5)
            response = await client.get(
                PDL_ENRICH_URL,
                params=query.as_params(self.min_likelihood),
                headers={"X-Api-Key": self.api_key},
                timeout=self.timeout,
            )
        return _result_from_response(response)


def _result_from_response(response: httpx.Response) -> PdlPersonResult:
    raw_body = response.content
    payload: dict[str, Any] | None = None
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            payload = parsed
    except ValueError:
        payload = None

    if response.status_code == 200:
        data = payload.get("data") if payload else None
        likelihood = payload.get("likelihood") if payload else None
        return PdlPersonResult(
            status_code=200,
            likelihood=int(likelihood) if isinstance(likelihood, int | float) else None,
            data=data if isinstance(data, dict) else payload,
            error=None,
            raw_body=raw_body,
        )
    if response.status_code == 404:
        return PdlPersonResult(response.status_code, None, None, "pdl_no_match", raw_body)
    if response.status_code == 402:
        return PdlPersonResult(response.status_code, None, None, "pdl_out_of_credits", raw_body)
    return PdlPersonResult(
        response.status_code,
        None,
        None,
        f"http_{response.status_code}",
        raw_body,
    )
