"""表示用アプリVersion。予測履歴のモデルVersionとは分離する。"""

APP_VERSION = "Version8"

# APP_VERSIONを変えない保守deployでも、長寿命Streamlit processが更新対象を
# 判別できるようにする。現在Versionの別定義ではなくimport契約のschema番号。
APP_RUNTIME_SCHEMA_VERSION = 1
