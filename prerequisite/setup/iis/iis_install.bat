@echo off
:: Install IIS with required modules for Python reverse proxy
echo Installing IIS and required modules...

dism /online /enable-feature /featurename:IIS-WebServerRole /All /NoRestart
dism /online /enable-feature /featurename:IIS-WebServer /All /NoRestart
dism /online /enable-feature /featurename:IIS-CommonHttpFeatures /All /NoRestart
dism /online /enable-feature /featurename:IIS-HttpErrors /All /NoRestart
dism /online /enable-feature /featurename:IIS-HttpRedirect /All /NoRestart
dism /online /enable-feature /featurename:IIS-ApplicationDevelopment /All /NoRestart
dism /online /enable-feature /featurename:IIS-CGI /All /NoRestart
dism /online /enable-feature /featurename:IIS-ISAPIExtensions /All /NoRestart
dism /online /enable-feature /featurename:IIS-ISAPIFilter /All /NoRestart
dism /online /enable-feature /featurename:IIS-WebServerManagementTools /All /NoRestart
dism /online /enable-feature /featurename:IIS-ManagementConsole /All /NoRestart
dism /online /enable-feature /featurename:IIS-ManagementService /All /NoRestart

echo IIS installation complete.
echo.
echo Now install ARR and URL Rewrite:
echo   - ARR:         prerequisite\setup\arr\ARRv3_setup_amd64_en-us.exe
echo   - URL Rewrite: prerequisite\setup\urlrewrite\rewrite_amd64_en-US.msi

net start W3SVC
echo IIS service started.
