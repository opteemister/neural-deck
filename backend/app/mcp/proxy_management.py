"""External management tools for all three tabs of the Proxy page."""

from typing import Annotated, Any

from pydantic import Field

from ..proxy.configuration import ProxyConfiguration
from ..schemas.proxy_management import ComfySourceRequest, ConnectionRequest, MediaAliasRequest


def build_proxy_operations(app: Any) -> dict[str, Any]:
    def config():
        runtime = app.state.runtime
        return ProxyConfiguration(runtime.proxy_service, runtime.comfy_service)

    async def get_proxy_route(route_name):
        return await app.state.runtime.proxy_service.get_route(route_name)

    async def list_proxy_model_choices(route_name, refresh):
        return await app.state.runtime.proxy_service.model_choices(route_name, refresh)

    async def list_openai_connections():
        return {"items": app.state.runtime.proxy_service.connections().list()}

    async def save_openai_connection(connection, connection_id):
        return app.state.runtime.proxy_service.connections().save(
            connection_id, connection.name, connection.base_url,
            connection.api_key.get_secret_value() if connection.api_key is not None else None,
        )

    async def delete_openai_connection(connection_id):
        app.state.runtime.proxy_service.delete_connection(connection_id)
        return {"id": connection_id, "deleted": True}

    async def list_openai_connection_models(connection_id, refresh):
        return {"items": await app.state.runtime.proxy_service.connections().models(connection_id, refresh=refresh)}

    async def get_proxy_media_config():
        return config().get()

    async def save_comfy_source(source, previous_name):
        return config().save_source(source, previous_name)

    async def delete_comfy_source(name):
        return config().delete_source(name)

    async def get_comfy_source_status(source):
        manager = config()
        manager.get()
        return await manager.comfy.source_status(source)

    async def list_comfy_workflows(source):
        manager = config()
        manager.get()
        return await manager.comfy.workflows(source)

    async def validate_comfy_workflow(source, path):
        manager = config()
        manager.get()
        return await manager.comfy.validate_workflow(source, path)

    async def save_media_alias(alias, previous_name):
        return await config().save_alias(alias, previous_name)

    async def delete_media_alias(name):
        return config().delete_alias(name)

    return {function.__name__: function for function in (
        get_proxy_route, list_proxy_model_choices, list_openai_connections,
        save_openai_connection, delete_openai_connection, list_openai_connection_models,
        get_proxy_media_config, save_comfy_source, delete_comfy_source,
        get_comfy_source_status, list_comfy_workflows, validate_comfy_workflow,
        save_media_alias, delete_media_alias,
    )}


PageSize = Annotated[int | None, Field(description="Число элементов страницы; null использует настройку сервера.")]
Cursor = Annotated[str | None, Field(description="next_cursor предыдущей страницы с теми же фильтрами; null начинает список.")]


def register_proxy_tools(server, call, paged, readonly, mutation, destructive):
    @server.tool(name="nd_get_proxy_route", description="Прочитать маршрут: кандидаты в порядке выбора, ID и диагностика.", annotations=readonly)
    async def get_proxy_route(route_name: Annotated[str, Field(description="Имя маршрута из nd_list_proxy_routes.")]):
        return await call("get_proxy_route", route_name)

    @server.tool(name="nd_list_proxy_model_choices", description="Общий список локальных и OpenAI-моделей для добавления в алиас; target передаётся в candidates.", annotations=readonly)
    async def list_proxy_model_choices(
        route_name: Annotated[str | None, Field(description="Имя редактируемого алиаса для включения его недоступных кандидатов; null для нового.")] = None,
        refresh: Annotated[bool, Field(description="Заново запросить каталоги OpenAI-подключений.")] = False,
        page_size: PageSize = None,
        cursor: Cursor = None,
    ):
        return await paged("list_proxy_model_choices", page_size, cursor, route_name, refresh)

    @server.tool(name="nd_list_openai_connections", description="Список OpenAI-подключений: ID, имя, URL и наличие ключа; без самих ключей.", annotations=readonly)
    async def list_openai_connections(page_size: PageSize = None, cursor: Cursor = None):
        return await paged("list_openai_connections", page_size, cursor)

    @server.tool(name="nd_save_openai_connection", description="Создать или изменить OpenAI-подключение. Проверить доступность и модели можно через nd_list_openai_connection_models.", annotations=mutation)
    async def save_openai_connection(
        connection: Annotated[ConnectionRequest, Field(description="Имя, URL API и ключ подключения.")],
        connection_id: Annotated[str | None, Field(description="ID изменяемого подключения; null создаёт новое.")] = None,
    ):
        return await call("save_openai_connection", connection, connection_id)

    @server.tool(name="nd_delete_openai_connection", description="Удалить OpenAI-подключение, если оно не используется алиасами.", annotations=destructive)
    async def delete_openai_connection(connection_id: Annotated[str, Field(description="ID из nd_list_openai_connections.")]):
        return await call("delete_openai_connection", connection_id)

    @server.tool(name="nd_list_openai_connection_models", description="Модели и capabilities OpenAI-подключения; ошибка описывает причину недоступности.", annotations=readonly)
    async def list_openai_connection_models(
        connection_id: Annotated[str, Field(description="ID сохранённого OpenAI-подключения.")],
        refresh: Annotated[bool, Field(description="true обновляет каталог с сервера, false допускает актуальный кэш.")] = False,
        page_size: PageSize = None,
        cursor: Cursor = None,
    ):
        return await paged("list_openai_connection_models", page_size, cursor, connection_id, refresh)

    @server.tool(name="nd_get_proxy_media_config", description="Прочитать подключения ComfyUI и все image/video алиасы с workflow и лимитами.", annotations=readonly)
    async def get_proxy_media_config():
        return await call("get_proxy_media_config")

    @server.tool(name="nd_save_comfy_source", description="Сохранить имя и URL ComfyUI-подключения. Переименование обновляет алиасы; имя и URL стандартного local фиксированы.", annotations=mutation)
    async def save_comfy_source(
        source: Annotated[ComfySourceRequest, Field(description="Имя и URL подключения ComfyUI.")],
        previous_name: Annotated[str | None, Field(description="Текущее имя изменяемого подключения; null создаёт новое.")] = None,
    ):
        return await call("save_comfy_source", source, previous_name)

    @server.tool(name="nd_delete_comfy_source", description="Удалить неиспользуемое ComfyUI-подключение; стандартный local остаётся.", annotations=destructive)
    async def delete_comfy_source(name: Annotated[str, Field(description="Имя подключения из sources в nd_get_proxy_media_config.")]):
        return await call("delete_comfy_source", name)

    @server.tool(name="nd_get_comfy_source_status", description="Проверить доступность сохранённого ComfyUI-подключения.", annotations=readonly)
    async def get_comfy_source_status(source: Annotated[str, Field(description="Имя подключения ComfyUI, включая local.")]):
        return await call("get_comfy_source_status", source)

    @server.tool(name="nd_list_comfy_workflows", description="Обновить список workflow через ComfyUI API; при недоступности вернуть кэш с available/cached.", annotations=readonly)
    async def list_comfy_workflows(
        source: Annotated[str, Field(description="Имя подключения ComfyUI, включая local.")],
        page_size: PageSize = None,
        cursor: Cursor = None,
    ):
        return await paged("list_comfy_workflows", page_size, cursor, source)

    @server.tool(name="nd_validate_comfy_workflow", description="Проверить API-формат workflow: usable true/false; null означает недоступность сервера.", annotations=readonly)
    async def validate_comfy_workflow(
        source: Annotated[str, Field(description="Имя подключения ComfyUI.")],
        path: Annotated[str, Field(description="Относительный путь из nd_list_comfy_workflows.")],
    ):
        return await call("validate_comfy_workflow", source, path)

    @server.tool(name="nd_save_media_alias", description="Сохранить image/video алиас: операции ND.v1 и максимум МП для image либо разрешение/секунды для video. Максимумы задаются явно; workflow исполняется как чёрный ящик.", annotations=mutation)
    async def save_media_alias(
        alias: Annotated[MediaAliasRequest, Field(description="Тип, имя, подключение, workflow операций и пределы генерации.")],
        previous_name: Annotated[str | None, Field(description="Текущее имя изменяемого алиаса; null создаёт новый.")] = None,
    ):
        return await call("save_media_alias", alias, previous_name)

    @server.tool(name="nd_delete_media_alias", description="Удалить image/video алиас; workflow и готовые файлы ComfyUI сохраняются.", annotations=destructive)
    async def delete_media_alias(name: Annotated[str, Field(description="Имя image/video алиаса из nd_get_proxy_media_config.")]):
        return await call("delete_media_alias", name)
