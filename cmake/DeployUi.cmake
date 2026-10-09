if(NOT DEFINED CN_UI_SOURCE OR NOT IS_DIRECTORY "${CN_UI_SOURCE}")
    message(FATAL_ERROR "Engineering UI source directory not found: '${CN_UI_SOURCE}'")
endif()

if(NOT DEFINED CN_UI_DEST OR "${CN_UI_DEST}" STREQUAL "")
    message(FATAL_ERROR "CN_UI_DEST was not provided")
endif()

if(NOT EXISTS "${CN_UI_SOURCE}/app/main.py" OR
   NOT EXISTS "${CN_UI_SOURCE}/app/server.py" OR
   NOT EXISTS "${CN_UI_SOURCE}/requirements.txt")
    message(FATAL_ERROR
        "Engineering UI source is incomplete. Expected app/main.py, app/server.py and requirements.txt in '${CN_UI_SOURCE}'")
endif()

file(MAKE_DIRECTORY "${CN_UI_DEST}")

file(COPY "${CN_UI_SOURCE}/"
    DESTINATION "${CN_UI_DEST}"
    PATTERN ".venv" EXCLUDE
    PATTERN "__pycache__" EXCLUDE
    PATTERN "*.pyc" EXCLUDE
    PATTERN ".pytest_cache" EXCLUDE
    PATTERN "data" EXCLUDE
)

message(STATUS "Engineering UI deployed to: ${CN_UI_DEST}")
