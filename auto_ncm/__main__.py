"""支持 ``python -m auto_ncm`` 直接运行。"""
from .main import main

if __name__ == "__main__":
    raise SystemExit(main())
