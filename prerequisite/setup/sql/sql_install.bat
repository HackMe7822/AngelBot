@echo off
:: SQL Server 2019 Express Silent Install
:: Instance name: ANGELBOT | SA password passed as %1
setlocal

SET INSTANCE_NAME=ANGELBOT
SET SA_PASS=%~1

IF "%SA_PASS%"=="" (
    echo ERROR: SA password required as first argument.
    exit /b 1
)

echo Installing SQL Server 2019 Express (instance: %INSTANCE_NAME%)...

SET SETUP_DIR=%~dp0
SET EXE=

:: Try to find setup exe in same folder
FOR %%f IN ("%SETUP_DIR%SQLEXPR_x64_ENU.exe" "%SETUP_DIR%SQLServer2019-SSEI-Expr.exe") DO (
    IF EXIST "%%f" SET EXE=%%f
)

IF "%EXE%"=="" (
    echo Setup EXE not found in %SETUP_DIR%
    echo Download SQL Server 2019 Express from:
    echo https://go.microsoft.com/fwlink/p/?linkid=866658
    echo Place it as SQLEXPR_x64_ENU.exe in this folder.
    exit /b 1
)

"%EXE%" /Q /IACCEPTSQLSERVERLICENSETERMS /ACTION=Install /FEATURES=SQLEngine ^
    /INSTANCENAME=%INSTANCE_NAME% /SQLSYSADMINACCOUNTS="%USERDOMAIN%\%USERNAME%" ^
    /SECURITYMODE=SQL /SAPWD="%SA_PASS%" /TCPENABLED=1 /BROWSERSVCSTARTUPTYPE=Automatic

IF %ERRORLEVEL% EQU 0 (
    echo SQL Server installed successfully.
    echo Instance: %INSTANCE_NAME%
    echo Enabling TCP/IP on port 1433...
    powershell -Command "& {
        \$smo = 'Microsoft.SqlServer.Management.Smo';
        [Reflection.Assembly]::LoadWithPartialName(\$smo + '.Smo') | Out-Null;
        \$wmi = New-Object (\$smo + '.Wmi.ManagedComputer');
        \$uri = 'ManagedComputer[@Name=\"' + \$env:COMPUTERNAME + '\"]/ServerInstance[@Name=\"%INSTANCE_NAME%\"]/ServerProtocol[@Name=\"Tcp\"]';
        \$Tcp = \$wmi.GetSmoObject(\$uri);
        \$Tcp.IsEnabled = \$true;
        \$Tcp.Alter();
    }"
    net stop "SQL Server (%INSTANCE_NAME%)" & net start "SQL Server (%INSTANCE_NAME%)"
    echo SQL Server ready on .\%INSTANCE_NAME%
) ELSE (
    echo ERROR: SQL Server installation failed. Check setup logs in %%TEMP%%.
    exit /b 1
)
endlocal
