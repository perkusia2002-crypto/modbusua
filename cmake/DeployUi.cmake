if(NOT DEFINED CN_UI_SOURCE OR NOT IS_DIRECTORY "${CN_UI_SOURCE}")
    message(FATAL_ERROR "Engineering UI source directory not found: '${CN_UI_SOURCE}'")
endif()

if(NOT DEFINED CN_UI_DEST OR "${CN_UI_DEST}" STREQUAL "")
    message(FATAL_ERROR "CN_UI_DEST was not provided")
endif()

set(_required_ui_files
    "app/main.py"
    "app/server.py"
    "app/auth.py"
    "app/config_manager.py"
    "app/modbus_service.py"
    "app/opcua_service.py"
    "app/process_manager.py"
    "requirements.txt"
    "static/index.html"
    "static/login.html"
    "static/app.js"
    "static/styles.css"
    "conf/modbusua.conf"
)
foreach(_file IN LISTS _required_ui_files)
    if(NOT EXISTS "${CN_UI_SOURCE}/${_file}")
        message(FATAL_ERROR "Engineering UI source is incomplete: missing '${CN_UI_SOURCE}/${_file}'")
    endif()
endforeach()

file(MAKE_DIRECTORY "${CN_UI_DEST}")
file(MAKE_DIRECTORY "${CN_UI_DEST}/conf")

# Runtime settings belong to the built application. Do not overwrite them on
# every build after the operator edits devices, ports or itemfile references.
file(COPY "${CN_UI_SOURCE}/"
    DESTINATION "${CN_UI_DEST}"
    PATTERN ".venv" EXCLUDE
    PATTERN "__pycache__" EXCLUDE
    PATTERN "*.pyc" EXCLUDE
    PATTERN ".pytest_cache" EXCLUDE
    PATTERN "data" EXCLUDE
    PATTERN ".env" EXCLUDE
    PATTERN "modbusua.conf" EXCLUDE
    PATTERN "items" EXCLUDE
)

if(NOT EXISTS "${CN_UI_DEST}/conf/modbusua.conf")
    file(COPY "${CN_UI_SOURCE}/conf/modbusua.conf" DESTINATION "${CN_UI_DEST}/conf")
endif()

if(IS_DIRECTORY "${CN_UI_SOURCE}/conf/items" AND NOT EXISTS "${CN_UI_DEST}/conf/items")
    file(COPY "${CN_UI_SOURCE}/conf/items" DESTINATION "${CN_UI_DEST}/conf")
endif()

message(STATUS "Engineering UI deployed to: ${CN_UI_DEST}")
