# Vercel serverless 入口：复用根目录的 Flask app。
# @vercel/python 会识别本模块里的 WSGI 变量 app 并对外提供服务。
from app import app  # noqa: F401
