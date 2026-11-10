"""数据源适配器（把游戏协议翻译成领域模型）。

- ``broadcast``：ACC Broadcasting（UDP）→ ``StandingsSnapshot``
- ``fixture`` / ``replay``：把原始报文落盘 / 回放，做离线的确定性测试底座

本包允许 import ``pyACCSharedMemory``，但**不允许** import ``backend``。
"""
