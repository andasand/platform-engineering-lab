# Platform Engineering Lab

A progressive hands-on lab for building, securing, automating, observing, and troubleshooting a production-style Kubernetes platform.

The project is designed around a simple learning cycle:

> **Build it → Break it → Explain it**

Rather than deploying a complete platform at once, each capability is introduced incrementally, tested, deliberately broken, diagnosed, and documented.

---

## Purpose

The primary goal of this project is to develop practical platform engineering skills while exploring how modern Kubernetes platforms can implement concepts described in U.S. Department of Defense DevSecOps and Zero Trust guidance.

The lab uses the **DoD Enterprise DevSecOps Fundamentals v2.5 (October 2024)** as current foundational guidance.

The **DoD Enterprise DevSecOps Reference Design: CNCF Kubernetes v2.1 (2021)** is used as an architectural reference for Kubernetes-based software factories.

The lab does **not** represent an authorized DoD environment or claim compliance, certification, or an Authority to Operate (ATO).

Instead, it provides an environment for experimenting with architecture, controls, automation, testing, telemetry, and evidence generation.

---

## Architecture

The platform currently runs locally using **kind (Kubernetes IN Docker)**.

```text
Developer
    |
    | kubectl
    v
Docker
    |
    v
kind Kubernetes Cluster
    |
    +-- Kubernetes Control Plane
    |      |
    |      +-- API Server
    |      +-- etcd
    |      +-- Scheduler
    |      +-- Controller Manager
    |
    +-- CoreDNS
    |
    +-- kindnet
    |
    +-- EventFlow Deployment
           |
           +-- Pod
           +-- Pod
           +-- Pod
                  |
                  v
             FastAPI :8000
```

The current application workload is **EventFlow**, a small FastAPI application intentionally kept simple so the focus remains on the platform.

---

## EventFlow

EventFlow currently provides a health endpoint:

```http
GET /health
```

Example:

```json
{
  "status": "healthy",
  "message": "EventFlow is running.",
  "hostname": "eventflow-76787b8f76-kjxl2"
}
```

The hostname makes it possible to observe which Kubernetes replica handles a request.

The application will gradually evolve as additional platform capabilities are introduced.

---

## Kubernetes Deployment

EventFlow currently runs as a three-replica Kubernetes Deployment.

```text
Client
   |
   | eventflow
   v
CoreDNS
   |
   v
Service
ClusterIP :80
   |
   v
EndpointSlice
   |
   +----> EventFlow Pod :8000
   +----> EventFlow Pod :8000
   +----> EventFlow Pod :8000
```

Readiness and liveness probes monitor the `/health` endpoint.

---

## What We Have Tested

### Containerization

EventFlow was packaged as a Docker image using `python:3.12-slim`.

The application was tested locally before deployment to Kubernetes.

### Kubernetes Desired State

Deleting an EventFlow pod demonstrated reconciliation:

```text
Desired replicas: 3

Pod deleted
     |
     v
ReplicaSet detects 2/3
     |
     v
New Pod created
     |
     v
Actual state returns to 3/3
```

### Image Troubleshooting

An incorrect image reference produced:

```text
ErrImagePull
ImagePullBackOff
```

This demonstrated the relationship between Kubernetes manifests, local images, image tags, and the kubelet's image resolution behavior.

### Application Failure

A Python syntax error caused:

```text
CrashLoopBackOff
```

Container logs were used to identify and correct the application failure.

### Readiness and Liveness

Readiness and liveness probes were added to EventFlow.

A deliberately incorrect readiness path demonstrated that a container can be:

```text
Running
```

while not being:

```text
Ready
```

and therefore not eligible to receive Service traffic.

### Kubernetes DNS

A troubleshooting pod successfully resolved:

```text
eventflow.default.svc.cluster.local
```

to the EventFlow Service ClusterIP.

This demonstrated Kubernetes service discovery through CoreDNS.

### Service and Endpoint Troubleshooting

The EventFlow Service uses:

```yaml
selector:
  app: eventflow
```

Changing the selector to:

```yaml
selector:
  app: eventflow-broken
```

caused the Service to have no matching EventFlow endpoints.

DNS continued to resolve successfully, demonstrating the distinction between:

```text
DNS discovery
      |
Service
      |
Pod selection
      |
EndpointSlice
```

This produced a useful troubleshooting sequence:

```text
Can DNS resolve?
       |
       v
Does the Service exist?
       |
       v
Are EndpointSlices populated?
       |
       v
Do labels and selectors match?
       |
       v
Can the Pod/application respond?
```

### Direct Pod Networking

A temporary troubleshooting pod was used to communicate directly with EventFlow Pod IP addresses:

```text
curl-test
     |
     +----> 10.244.0.2:8000
     +----> 10.244.0.5:8000
     +----> 10.244.0.6:8000
```

This bypassed CoreDNS and the Kubernetes Service and demonstrated direct pod-network communication.

### NetworkPolicy Experiment

A default-deny ingress NetworkPolicy was created for:

```text
app=eventflow
```

Kubernetes successfully accepted the policy:

```text
Selected pods are isolated for ingress connectivity
```

However, traffic continued to reach EventFlow.

Investigation showed that the current kind cluster uses:

```text
kindnet
```

as its networking implementation.

The experiment demonstrates an important platform engineering principle:

> **Defining a policy is not the same as enforcing a policy.**

The networking implementation must provide the enforcement capability required by the policy.

---

## Zero Trust Direction

The DoD Enterprise DevSecOps Fundamentals v2.5 identifies Zero Trust as the target security model for DevSecOps software factories and platforms.

As the lab evolves, Zero Trust concepts will be introduced progressively, including:

- deny-by-default communication
- workload and non-person entity identity
- east-west traffic controls
- Kubernetes RBAC
- NetworkPolicy
- workload authentication
- mutual TLS (mTLS)
- secrets management
- continuous monitoring
- policy enforcement and telemetry

The goal is to understand not only how to configure these controls, but also where and how they are actually enforced.

---

## Roadmap

```text
[✓] Containerized application
[✓] Kubernetes Deployment
[✓] Services and EndpointSlices
[✓] Readiness and liveness probes
[✓] Kubernetes DNS
[✓] Pod-to-Pod networking
[✓] Failure and troubleshooting exercises

[ ] Policy-capable Kubernetes networking (Cilium)
[ ] Default-deny NetworkPolicy
[ ] Explicit east-west allow policies
[ ] Persistent storage / PostgreSQL
[ ] Helm
[ ] CI/CD software factory
[ ] Artifact repository
[ ] Software supply-chain security
[ ] GitOps / Argo CD
[ ] Kubernetes RBAC
[ ] Secrets and workload identity
[ ] Observability
[ ] Continuous security monitoring
[ ] Service mesh / mTLS
[ ] OpenShift
[ ] Infrastructure as Code
[ ] High availability and SRE exercises
[ ] NIST SP 800-53 control mapping
[ ] Zero Trust policy architecture
[ ] RMF / cATO-style evidence exercises
[ ] DevSecOps software-factory capstone
```

---

## Next Mission: Cilium

The current kind cluster uses kindnet.

The next networking exercise will replace the default networking configuration with **Cilium** so that NetworkPolicy can be enforced.

The same experiment will then be repeated:

```text
Normal traffic
      |
      v
Default-deny policy
      |
      v
Traffic blocked
      |
      v
Explicit allow policy
      |
      v
Traffic restored
```

This will provide the lab's first practical implementation of deny-by-default east-west workload communication.

---

## Repository Structure

```text
platform-engineering-lab/
├── app/
│   └── main.py
├── docs/
│   ├── architecture/
│   ├── compliance/
│   └── security/
├── kubernetes/
│   └── base/
│       ├── deployment.yaml
│       ├── service.yaml
│       └── networkpolicy.yaml
├── tests/
├── Dockerfile
├── requirements.txt
└── README.md
```

The repository will expand as additional platform capabilities are introduced.

---

## References

- DoD Enterprise DevSecOps Fundamentals v2.5, October 2024
- DoD Enterprise DevSecOps Reference Design: CNCF Kubernetes v2.1, September 2021
- DoD Zero Trust Strategy
- NIST SP 800-53, Security and Privacy Controls for Information Systems and Organizations
- NIST SP 800-190, Application Container Security Guide
- NIST SP 800-218, Secure Software Development Framework
- Kubernetes Documentation
- kind Documentation
- Cilium Documentation