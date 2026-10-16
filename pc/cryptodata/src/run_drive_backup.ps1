$ErrorActionPreference='Stop'
$log=Join-Path 'E:\CryptoData tai nguyen\reports' ('drive-backup-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log')
& 'C:\Users\tctha\AppData\Local\Python\pythoncore-3.14-64\python.exe' 'E:\CryptoData\src\daily_drive_backup.py' *> $log
exit $LASTEXITCODE
