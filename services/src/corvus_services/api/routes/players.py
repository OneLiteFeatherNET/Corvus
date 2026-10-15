"""Players (lookup, erasure), blocklist/allowlist (role: admin) and the proxy blocklist feed."""

from __future__ import annotations

import hashlib
import logging
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..auth import Principal, Role, require, require_proxy_token
from ..deps import Deps, get_deps, get_page
from ..erasure import erase_player
from ..pagination import PageRequest
from ..ports import BlockEntry
from ..problems import ProblemError
from ..pseudonym import PseudonymKeyMissingError, parse_pseudonym
from ..schemas import (
    BlockEntryOut,
    ErasureOut,
    Paged,
    PlayerLookupOut,
    PluginBlocklistOut,
)

log = logging.getLogger("corvus.api.players")

router = APIRouter(tags=["players"])
plugin_router = APIRouter(tags=["plugin"])
_ADMIN = Depends(require(Role.ADMIN))


class LookupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uuid: UUID = Field(description="Minecraft UUID; used in memory only, never stored or logged")


class ErasureIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uuid: UUID | None = Field(default=None, description="Minecraft UUID (needs the pseudonym key)")
    pseudonym: str | None = Field(default=None, description="64 hex characters")

    @model_validator(mode="after")
    def _exactly_one(self) -> ErasureIn:
        if (self.uuid is None) == (self.pseudonym is None):
            raise ValueError("provide exactly one of uuid and pseudonym")
        if self.pseudonym is not None:
            parse_pseudonym(self.pseudonym)
        return self


def _resolve(deps: Deps, player: UUID) -> bytes:
    try:
        return deps.pseudonymizer.pseudonym(player)
    except PseudonymKeyMissingError as e:
        raise ProblemError(
            503, "the pseudonym key is not configured", slug="pseudonym-key-missing"
        ) from e


def _pseudonym_or_422(value: str) -> bytes:
    try:
        return parse_pseudonym(value)
    except ValueError as e:
        raise ProblemError(422, str(e), slug="validation") from e


@router.post(
    "/players/lookup",
    response_model=PlayerLookupOut,
    summary="Resolve a Minecraft UUID to its pseudonym (admin)",
    description="POST so that the UUID never appears in URLs or access logs.",
)
async def lookup_player(
    body: LookupIn, who: Principal = _ADMIN, deps: Deps = Depends(get_deps)
) -> PlayerLookupOut:
    pseudonym = _resolve(deps, body.uuid)
    player = await deps.repos.players.get(pseudonym)
    log.info("player lookup by %s -> pseudonym %s", who.subject, pseudonym.hex()[:12])
    return PlayerLookupOut.of(pseudonym, deps.pseudonymizer.key_id, player)


@router.post(
    "/players/erasure",
    status_code=202,
    response_model=ErasureOut,
    summary="Erase a player (admin)",
    description=(
        "In one transaction: deletes the player's key (crypto-shredding), sets `players.erased_at`, "
        "blocklists the pseudonym and records an audit row. Compaction of `events` files is a later "
        "job; `compaction_pending` stays true until then."
    ),
)
async def erase(
    body: ErasureIn, who: Principal = _ADMIN, deps: Deps = Depends(get_deps)
) -> ErasureOut:
    pseudonym = _resolve(deps, body.uuid) if body.uuid else parse_pseudonym(body.pseudonym or "")
    request = await erase_player(
        deps.repos.erasure,
        pseudonym,
        requested_by=who.subject,
        now=deps.clock(),
        new_id=deps.new_id,
    )
    deps.metrics.erasures.inc()
    log.info("erasure %s by %s pseudonym %s", request.id, who.subject, pseudonym.hex()[:12])
    return ErasureOut.of(request)


@router.get(
    "/players/erasure/{request_id}",
    response_model=ErasureOut,
    responses={404: {"description": "Unknown request"}},
    summary="Get an erasure request (admin)",
)
async def get_erasure(
    request_id: UUID, _: Principal = _ADMIN, deps: Deps = Depends(get_deps)
) -> ErasureOut:
    request = await deps.repos.erasure.get_request(request_id)
    if request is None:
        raise ProblemError(404, "erasure request not found", slug="not-found")
    return ErasureOut.of(request)


class BlockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["block", "allow"]
    reason: str = Field(default="", max_length=500)


@router.get(
    "/blocklist", response_model=Paged[BlockEntryOut], summary="List blocklist and allowlist"
)
async def list_blocklist(
    kind: Literal["block", "allow"] | None = Query(default=None),
    _: Principal = _ADMIN,
    page: PageRequest = Depends(get_page),
    deps: Deps = Depends(get_deps),
) -> Paged[BlockEntryOut]:
    result = await deps.repos.blocklist.list_page(page, kind)
    return Paged[BlockEntryOut](
        items=[BlockEntryOut.of(b) for b in result.items], next_cursor=result.next_cursor
    )


@router.get(
    "/blocklist/{pseudonym}",
    response_model=BlockEntryOut,
    responses={404: {"description": "Not listed"}},
    summary="Get one entry",
)
async def get_block_entry(
    pseudonym: str, _: Principal = _ADMIN, deps: Deps = Depends(get_deps)
) -> BlockEntryOut:
    entry = await deps.repos.blocklist.get(_pseudonym_or_422(pseudonym))
    if entry is None:
        raise ProblemError(404, "pseudonym is not listed", slug="not-found")
    return BlockEntryOut.of(entry)


@router.put(
    "/blocklist/{pseudonym}",
    response_model=BlockEntryOut,
    responses={409: {"description": "Entry was created by an erasure and is permanent"}},
    summary="Add or change an entry",
)
async def put_block_entry(
    pseudonym: str, body: BlockIn, who: Principal = _ADMIN, deps: Deps = Depends(get_deps)
) -> BlockEntryOut:
    key = _pseudonym_or_422(pseudonym)
    existing = await deps.repos.blocklist.get(key)
    if existing is not None and existing.source == "erasure":
        raise ProblemError(
            409, "entries created by an erasure cannot be changed", slug="erasure-entry"
        )
    entry = BlockEntry(key, body.kind, "manual", body.reason, who.subject, deps.clock())
    await deps.repos.blocklist.put(entry)
    return BlockEntryOut.of(entry)


@router.delete(
    "/blocklist/{pseudonym}",
    status_code=204,
    responses={
        404: {"description": "Not listed"},
        409: {"description": "Entry was created by an erasure and is permanent"},
    },
    summary="Remove an entry",
)
async def delete_block_entry(
    pseudonym: str, _: Principal = _ADMIN, deps: Deps = Depends(get_deps)
) -> Response:
    key = _pseudonym_or_422(pseudonym)
    existing = await deps.repos.blocklist.get(key)
    if existing is None:
        raise ProblemError(404, "pseudonym is not listed", slug="not-found")
    if existing.source == "erasure":
        raise ProblemError(
            409, "entries created by an erasure cannot be removed", slug="erasure-entry"
        )
    await deps.repos.blocklist.delete(key)
    return Response(status_code=204)


@plugin_router.get(
    "/plugin/blocklist",
    response_model=PluginBlocklistOut,
    dependencies=[Depends(require_proxy_token)],
    summary="Blocklist feed for proxies (API token, no OIDC)",
)
async def plugin_blocklist(
    response: Response, deps: Deps = Depends(get_deps)
) -> PluginBlocklistOut:
    blocked, allowed = await deps.repos.blocklist.all_pseudonyms()
    digest = hashlib.sha256()
    for name, items in (("blocked", blocked), ("allowed", allowed)):
        digest.update(name.encode() + b"\n")
        for p in items:
            digest.update(p)
    response.headers["Cache-Control"] = "no-store"
    return PluginBlocklistOut(
        version=digest.hexdigest(),
        blocked=[p.hex() for p in blocked],
        allowed=[p.hex() for p in allowed],
    )
