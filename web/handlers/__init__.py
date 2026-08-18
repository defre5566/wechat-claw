"""web handlers：向导 6 步 + 管理 API（wizard.py 路由分发到各模块函数）。

每个 handler 模块提供 handle(app, body) -> dict 形式的处理函数（或 async），
wizard.py 的路由表负责 method/path → handler 映射。
"""
