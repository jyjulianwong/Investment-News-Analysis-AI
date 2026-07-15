FROM --platform=linux/amd64 public.ecr.aws/lambda/python:3.12

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

COPY agent.py ${LAMBDA_TASK_ROOT}/

CMD ["agent.handler"]
