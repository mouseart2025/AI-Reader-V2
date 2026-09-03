; Tauri v2 NSIS installer hooks (issue #71)
;
; PyInstaller onefile sidecar 是 bootloader + 子进程结构;若主程序异常退出,
; 子进程可能残留并持续占用 sidecar exe,导致升级/卸载时文件无法替换,
; 出现「新前端 + 旧后端」混装(表现为新端点 404/405)。
; 安装与卸载前强制清理所有残留的 sidecar 进程。

!macro NSIS_HOOK_PREINSTALL
  nsExec::Exec 'taskkill /F /T /IM ai-reader-sidecar-x86_64-pc-windows-msvc.exe'
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  nsExec::Exec 'taskkill /F /T /IM ai-reader-sidecar-x86_64-pc-windows-msvc.exe'
!macroend
