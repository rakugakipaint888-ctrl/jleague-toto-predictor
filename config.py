"""旧importとの互換性を保つための設定モジュール。

アプリ本体は、Streamlit再実行時の新旧モジュール混在を避けるため
``model_config`` を直接importする。Version4までの利用コード向けに、
この名前からも同じ公開設定を参照できるようにしている。
"""

from model_config import *  # noqa: F401,F403
