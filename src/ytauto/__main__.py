import sys

from studio.cli.main import main

print("note: `python -m ytauto` is deprecated; use `python -m studio`", file=sys.stderr)
sys.exit(main())
