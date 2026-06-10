from phosphor_eda.domain.pcb import LayerRole, PcbLayer


def make_pcb_layer(
    name: str,
    role: LayerRole,
    side: str = "",
    number: int | None = None,
) -> PcbLayer:
    roles = [role]
    if side == "front":
        roles.append(LayerRole.FRONT)
    elif side == "back":
        roles.append(LayerRole.BACK)
    elif side == "inner" or (role is LayerRole.COPPER and not side):
        roles.append(LayerRole.INNER)
    return PcbLayer(name=name, roles=tuple(roles), number=number)
