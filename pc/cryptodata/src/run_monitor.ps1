$ErrorActionPreference='Stop'
$pythonExe='C:\Users\tctha\AppData\Local\Python\pythoncore-3.14-64\python.exe'
& $pythonExe 'E:\CryptoData\src\monitor_health.py'
$result=$LASTEXITCODE
if ($result -eq 10 -or $result -eq 11) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $notify=New-Object System.Windows.Forms.NotifyIcon
    $notify.Icon=[System.Drawing.SystemIcons]::Information
    $notify.Visible=$true
    $title='CryptoData: can kiem tra'
    $message='Mo dashboard hoac reports\health.json de xem tinh trang du lieu.'
    if ($result -eq 11) {$title='CryptoData: du lieu phuc hoi';$message='Cac nguon da cau hinh vuot qua kiem tra hien tai.'}
    $notify.ShowBalloonTip(5000,$title,$message,[System.Windows.Forms.ToolTipIcon]::Info)
    Start-Sleep -Seconds 6
    $notify.Dispose()
}
if ($result -in @(0,10,11)) {exit 0}
exit $result
