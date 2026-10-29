# TLS 证书（本地演练）

不要把真实私钥提交进 git。生成本地自签：

```powershell
.\scripts\gen_gateway_certs.ps1
```

生产把云厂商 / 内网 CA 的 `tls.crt`、`tls.key` 放到此目录，或改 nginx 挂载路径指向 Secret 卷。
