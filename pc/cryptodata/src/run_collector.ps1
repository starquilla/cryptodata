$ErrorActionPreference = 'Stop'
$pythonExe = 'C:\Users\tctha\AppData\Local\Python\pythoncore-3.14-64\python.exe'
$logPath = Join-Path 'E:\CryptoData tai nguyen\reports' ('scheduled-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
& $pythonExe 'E:\CryptoData\src\collect_incremental.py' *> $logPath
$collectResult = $LASTEXITCODE
& $pythonExe 'E:\CryptoData\src\collect_derivatives_history.py' *>> $logPath
$derivativesResult = $LASTEXITCODE
& $pythonExe 'E:\CryptoData\src\collect_research.py' *>> $logPath
$researchResult = $LASTEXITCODE
& $pythonExe 'E:\CryptoData\src\collect_extra_venues.py' *>> $logPath
$extraResult = $LASTEXITCODE
& $pythonExe 'E:\CryptoData\src\check_health.py' *>> $logPath
$healthResult = $LASTEXITCODE
& $pythonExe 'E:\CryptoData\src\audit_stability_identity.py' *>> $logPath
$auditResult = $LASTEXITCODE
& $pythonExe 'E:\CryptoData\src\build_dashboard.py' *>> $logPath
$dashboardResult = $LASTEXITCODE
foreach ($result in @($collectResult,$derivativesResult,$researchResult,$extraResult,$healthResult,$auditResult,$dashboardResult)) { if ($result -ne 0) { exit $result } }
exit 0

