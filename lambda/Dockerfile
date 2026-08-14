FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.12

ARG INA_VERSION=unknown
ENV INA_VERSION=${INA_VERSION}

# WeasyPrint system dependencies
RUN dnf install -y \
    pango \
    cairo \
    gdk-pixbuf2 \
    libffi-devel \
    libjpeg-turbo \
    fontconfig \
    && dnf clean all

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml .
RUN uv pip install --system --no-cache -r pyproject.toml

COPY agent.py helpers.py state.py ${LAMBDA_TASK_ROOT}/
COPY nodes/ ${LAMBDA_TASK_ROOT}/nodes/
COPY email_adapter/ ${LAMBDA_TASK_ROOT}/email_adapter/
COPY search_providers/ ${LAMBDA_TASK_ROOT}/search_providers/
COPY prompts/ ${LAMBDA_TASK_ROOT}/prompts/

CMD ["agent.handler"]
