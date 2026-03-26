# Acesso dos analistas ao ZIG Risk Monitor

Quando o app roda no seu PC, outros analistas acessam pela **Network URL** que o Streamlit imprime no terminal (ex.: `http://172.16.0.111:8501`). A porta **padrão** do Streamlit é **8501**; se ela estiver ocupada, o próximo run pode usar **8502**, **8503**, etc. **Sempre use exatamente** a URL e a porta mostradas na linha **Network URL** — não assuma 8502.

**Subir o app (pasta `zig_risk_monitor`):**

```powershell
cd "...\zig_risk_monitor"
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 0.0.0.0
```

O parâmetro `--server.address 0.0.0.0` é necessário para aceitar conexões de outros computadores na mesma rede.

## Checklist (quem está com o app rodando)

1. **App está rodando?**  
   O terminal onde você rodou `streamlit run app.py --server.address 0.0.0.0` deve estar aberto e sem mensagem de erro.

2. **Firewall do Windows**  
   O Firewall costuma bloquear conexões de outros PCs. No PC onde o app roda:
   - Confira na **Network URL** qual porta o Streamlit está usando (geralmente **8501**).
   - Edite **`zig_risk_monitor/scripts/liberar_porta_firewall.ps1`** e deixe **`$porta`** igual a essa porta.
   - Abra **PowerShell como Administrador** (botão direito → "Executar como administrador") e execute:
     ```powershell
     cd "c:\Users\BrunaAmaral\OneDrive - ZIG Tecnologia S A\bases_analise\base_cash_out\zig_risk_monitor\scripts"
     .\liberar_porta_firewall.ps1
     ```
   - Alternativa: **Painel de Controle → Firewall do Windows → Configurações avançadas → Regras de entrada → Nova regra** → Porta → TCP → **a porta exibida no terminal** → Permitir.

3. **IP e porta corretos**  
   No terminal onde o Streamlit está rodando, confira a linha **"Network URL"**. Use exatamente esse endereço (IP e porta). O IP pode mudar se você trocar de rede ou reconectar no Wi‑Fi.

4. **Mesma rede**  
   Os analistas precisam estar na **mesma rede** (mesmo Wi‑Fi/LAN) que o PC onde o app está rodando. VPN ou rede de casa vs escritório podem impedir o acesso.

5. **Antivírus / firewall corporativo**  
   Às vezes o antivírus ou o firewall da empresa bloqueia. Teste com o antivírus temporariamente desativado ou peça à TI para liberar no IP do seu PC a **mesma porta TCP** que aparece na Network URL (ex.: **8501**).

## Resumo

| Problema | O que fazer |
|----------|-------------|
| Link não abre para analistas | Liberar porta no Firewall (script `scripts/liberar_porta_firewall.ps1` ou regra manual). |
| IP ou porta mudou | Ver de novo a linha "Network URL" no terminal do Streamlit e enviar o link atualizado. |
| Só funciona no seu PC | Confirmar mesma rede e regra de firewall para a porta usada. |
