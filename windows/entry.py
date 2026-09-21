# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 CardsClaim contributors
from cardsclaim.desktop.app import main

if __name__ == '__main__':
    import sys
    try:
        main()
    except Exception:
        if '--gui-self-test' in sys.argv:
            import json
            import traceback
            from pathlib import Path
            target = Path(sys.argv[sys.argv.index('--gui-self-test') + 1])
            target.write_text(json.dumps({'ok': False, 'error': traceback.format_exc()}), encoding='utf-8')
            raise SystemExit(1)
        raise
