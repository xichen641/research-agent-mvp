from uuid import UUID, uuid4


EntityId = UUID


def new_id() -> EntityId:
    return uuid4()
