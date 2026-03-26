# Libera a porta do Streamlit no Firewall do Windows para outros PCs acessarem.
# Execute como Administrador: botão direito no PowerShell -> "Executar como administrador"

# Ajuste para a porta mostrada em "Network URL" no terminal (padrão Streamlit: 8501).
$porta = 8501
$nomeRegra = "ZIG Risk Monitor - Streamlit"

# Remove regra antiga se existir (evita duplicata)
Remove-NetFirewallRule -DisplayName $nomeRegra -ErrorAction SilentlyContinue

# Cria regra permitindo entrada na porta TCP
New-NetFirewallRule -DisplayName $nomeRegra -Direction Inbound -Protocol TCP -LocalPort $porta -Action Allow

Write-Host "Regra criada: entrada TCP na porta $porta permitida." -ForegroundColor Green
Write-Host "Outros PCs na mesma rede podem acessar: http://SEU_IP:$porta" -ForegroundColor Cyan
