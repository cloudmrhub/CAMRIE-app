# CAMRIE Mode 2 worker image.
#
# Layers the current app.py (with presigned-URL result delivery) on top of the
# already-built Mode 1 CPU image, so the Julia/KomaMRI stack is not rebuilt.
# Entry point is unchanged: ENTRYPOINT ["python", "app.py"] reading FILE_EVENT.
#
# Build from the repo root:
#   docker build -f worker/image/Dockerfile \
#     --build-arg BASE_IMAGE=<private camrie-fargate@sha256:...> \
#     -t camrie-fargate:mode2 calculation/src
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
COPY app.py /app/app.py
RUN python -c "import ast; ast.parse(open('/app/app.py').read()); print('app.py OK')"
