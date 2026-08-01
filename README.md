# ETL Pipeline Observability Platform on AKS

A self-contained project to practice Azure + Kubernetes + ETL support skills:
deploy a small ETL job on AKS, instrument it with Prometheus metrics, alert
on business SLAs and technical failures, and build a runbook from
deliberately simulated edge cases.

## Structure

```
etl-project/
├── src/
│   ├── etl_job.py          # ETL logic + Prometheus instrumentation
│   ├── requirements.txt
│   └── sample_data/source.json   # sample input with built-in edge cases
├── Dockerfile
├── k8s/
│   ├── 00-namespace.yaml
│   ├── 01-configmap.yaml
│   ├── 02-secretproviderclass.yaml   # Azure Key Vault via CSI driver
│   ├── 03-cronjob.yaml
│   ├── 04-rbac.yaml
│   └── 05-prometheusrule.yaml        # Alert rules
├── metrics-dictionary.md
├── runbook.md
└── README.md
```

## 1. Provision Azure infrastructure

```bash
az group create -n etl-rg -l eastus

az aks create -g etl-rg -n etl-aks \
  --node-count 2 \
  --enable-managed-identity \
  --enable-addons azure-keyvault-secrets-provider \
  --generate-ssh-keys

az aks get-credentials -g etl-rg -n etl-aks

az storage account create -g etl-rg -n etlstorageacct --sku Standard_LRS
az keyvault create -g etl-rg -n etl-kv -l eastus
az acr create -g etl-rg -n etlacr --sku Basic
az aks update -g etl-rg -n etl-aks --attach-acr etlacr
```

## 2. Install monitoring stack

```bash
kubectl create namespace monitoring

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm install kube-prometheus prometheus-community/kube-prometheus-stack \
  -n monitoring

helm install prometheus-pushgateway prometheus-community/prometheus-pushgateway \
  -n monitoring
```

## 3. Build and push the ETL image

```bash
az acr build -r etlacr -t etl-job:v1 .
```

Then edit `k8s/03-cronjob.yaml` and replace `<ACR_NAME>` with `etlacr`.

## 4. Populate Key Vault secrets (used by 02-secretproviderclass.yaml)

```bash
az keyvault secret set --vault-name etl-kv --name storage-connection-string --value "<your-connection-string>"
az keyvault secret set --vault-name etl-kv --name source-api-key --value "<your-api-key>"
```

Edit `02-secretproviderclass.yaml` and fill in `<MANAGED_IDENTITY_CLIENT_ID>` and `<AZURE_TENANT_ID>`. Grant the AKS kubelet identity `get`/`list` access on the Key Vault:

```bash
az keyvault set-policy -n etl-kv --secret-permissions get list --spn <MANAGED_IDENTITY_CLIENT_ID>
```

## 5. Deploy everything

```bash
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/01-configmap.yaml
kubectl apply -f k8s/02-secretproviderclass.yaml
kubectl apply -f k8s/04-rbac.yaml
kubectl apply -f k8s/03-cronjob.yaml
kubectl apply -f k8s/05-prometheusrule.yaml
```

## 6. Trigger a manual run (don't wait for the schedule)

```bash
kubectl create job --from=cronjob/etl-job etl-job-manual-1 -n etl
kubectl logs -n etl -l app=etl-job -f
```

## 7. View dashboards

```bash
kubectl port-forward -n monitoring svc/kube-prometheus-grafana 3000:80
```
Open http://localhost:3000 (default user `admin`, get password with):
```bash
kubectl get secret -n monitoring kube-prometheus-grafana -o jsonpath="{.data.admin-password}" | base64 -d
```

Import a dashboard or build panels querying the metrics listed in `metrics-dictionary.md`.

## 8. Simulate edge cases

The sample data in `src/sample_data/source.json` already contains:
- a duplicate record (id `2`)
- an unparseable timestamp (id `3`)
- a schema drift record with an unexpected field (id `4`)
- a stale record that will breach the freshness SLA (id `5`)

Run the job locally to see it in action:
```bash
cd src
pip install -r requirements.txt
SOURCE_PATH=./sample_data/source.json PUSHGATEWAY_URL=localhost:9091 python3 etl_job.py
```

Try the other scenarios in `runbook.md` (OOM kill, node drain, Key Vault rotation, network policy blocks) by deliberately breaking things in your cluster and documenting what you observe — that documentation is the actual deliverable of this project.

## Next steps to extend this project

- Replace the file-based extract/load with real Azure Blob/ADLS Gen2 calls using `azure-storage-blob`
- Add a `NetworkPolicy` restricting the `etl` namespace to only reach `monitoring` and Azure endpoints
- Add a Grafana dashboard JSON export to this repo so it's version-controlled
- Wire in Azure Monitor / Log Analytics for log-based alerting alongside Prometheus metric alerting
- Add a GitHub Actions workflow to build/push the image and `kubectl apply` on merge to `main`
