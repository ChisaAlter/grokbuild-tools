' Silent launcher — no console window
' Double-click this file to start Grok Account Manager

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)

pythonw = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonw) Then
  ' fallback: system pythonw
  pythonw = "pythonw.exe"
End If

' 0 = hidden window for the launcher process; pythonw has no console anyway
sh.CurrentDirectory = root
sh.Run """" & pythonw & """ -m grok_account_manager", 0, False
