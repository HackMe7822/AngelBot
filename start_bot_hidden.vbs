' Launches AngelBot silently in the background (no visible window).
' Use this for Task Scheduler so the bot runs without a console on screen.
' Logs are written to logs\angelbot.log

Dim shell
Set shell = CreateObject("WScript.Shell")
shell.CurrentDirectory = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\") - 1)
shell.Run "cmd /c python -u main.py >> logs\angelbot.log 2>&1", 0, False
Set shell = Nothing
