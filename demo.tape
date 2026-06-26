# VHS Tape - Global Loop Engine Demo
# 运行前请先安装 vhs: brew install vhs
# 运行命令生成 GIF: vhs demo.tape

Output loop_engine_demo.gif

Set FontSize 15
Set FontFamily "JetBrains Mono"
Set Width 1200
Set Height 650
Set Padding 30
Set Theme "Dracula"
Set WindowBar Rings

# 模拟 Agent 声称自己写好了代码
Type "echo 'Agent: I have perfectly fixed the JWT auth bug and run the tests!'"
Enter
Sleep 1s

# 运行引擎拦截
Type "loop-engine --task 'Fix JWT Auth' --mode fast"
Enter
Sleep 1s

# 模拟引擎输出红灯拦截
Type@0.05s "2026-06-25 10:00:01 [INFO] [PlannerNode] test baseline signature: 5edcfab..."
Enter
Type@0.05s "2026-06-25 10:00:02 [ERROR] [CriticNode] Test Failed! Exit code: 1"
Enter
Type@0.05s "2026-06-25 10:00:02 [WARNING] [TestIntegrityGuard] Agent tried to fake success!"
Enter
Sleep 1.5s

# 模拟 StateCompressor 工作
Type@0.05s "2026-06-25 10:00:03 [INFO] [StateCompressor] Compressing 15,420 tokens of pytest traceback..."
Enter
Type@0.05s "2026-06-25 10:00:04 [INFO] [StateCompressor] Compression complete. Extracted 540 tokens of core feedback."
Enter
Type@0.05s "2026-06-25 10:00:04 [INFO] [RollbackNode] Reverting Git state to clean baseline..."
Enter
Sleep 2s

# 清屏，模拟重试
Ctrl+L
Sleep 0.5s

Type "echo 'Agent: My bad. I read the compressed feedback and fixed the HMAC timing attack.'"
Enter
Sleep 1.5s

Type "loop-engine --task 'Fix JWT Auth' --mode fast"
Enter
Sleep 1s

# 绿灯通过
Type@0.05s "2026-06-25 10:05:01 [INFO] [CriticNode] Validation done. Exit: 0, Pass: True"
Enter
Type@0.05s "2026-06-25 10:05:02 [INFO] =================================================="
Enter
Type@0.05s "2026-06-25 10:05:02 [INFO] Engine Summary"
Enter
Type@0.05s "2026-06-25 10:05:02 [INFO] Status: PASSED ✅"
Enter
Type@0.05s "2026-06-25 10:05:02 [INFO] Total iterations: 2"
Enter
Type@0.05s "2026-06-25 10:05:02 [INFO] Estimated Cost: $0.04"
Enter

Sleep 3s
