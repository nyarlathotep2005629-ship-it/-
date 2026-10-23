from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from fastapi import status

from ..dependencies import get_runtime_controller
from ..schemas import RfidRegistrationStartRequest
from ..services.rfid_runtime import RfidRuntimeController

router = APIRouter(prefix="/rfid", tags=["rfid"])


@router.post("/registration/start", status_code=status.HTTP_202_ACCEPTED)
def start_registration(
    payload: RfidRegistrationStartRequest,
    controller: RfidRuntimeController = Depends(get_runtime_controller),
):
    return controller.start_registration(payload.name, payload.port)


@router.get("/ports")
def get_ports(controller: RfidRuntimeController = Depends(get_runtime_controller)):
    return controller.ports()


@router.get("/runtime")
def get_runtime(controller: RfidRuntimeController = Depends(get_runtime_controller)):
    return controller.snapshot()


@router.get("/events")
def get_events(
    server_instance_id: str | None = Query(default=None),
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    controller: RfidRuntimeController = Depends(get_runtime_controller),
):
    return controller.events(server_instance_id, after_sequence, limit)


@router.post("/runtime/stop")
def stop_runtime(controller: RfidRuntimeController = Depends(get_runtime_controller)):
    return controller.stop()
