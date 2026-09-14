# Platform Engineering Lab Architecture

## Purpose

The goal of this project is to implement, test, and learn the DoD Enterprise DevSecOps Reference Design: CNCF Kubernetes.

The lab will progressively build a containerized application, CI/CD pipeline, Kubernetes-based runtime platform, security controls, observability, and infrastructure automation in order to understand how the reference design works in practice.

The project will emphasize hands-on implementation, failure testing, troubleshooting, and security/compliance concepts rather than simply reproducing diagrams or configuration examples.

## High-Level Architecture

Developer
   |
   v
Source Control
   |
   v
DevSecOps Pipeline
   |
   v
Container Registry
   |
   v
Kubernetes Platform
   |
   v
Application Workloads
   |
   v
Users

## Control Plane and Local kind Architecture

This lab uses **kind (Kubernetes IN Docker)** to provide a local Kubernetes
environment. kind runs Kubernetes nodes as Docker containers. In this lab,
the cluster is named `platform-lab`, and its control-plane node runs as the
Docker container:

`platform-lab-control-plane`

### Architecture

```text
Developer / kubectl
        |
        | Kubernetes API
        v
+----------------------------------+
| Docker                           |
|                                  |
| platform-lab-control-plane       |
|                                  |
|  +----------------------------+  |
|  | Kubernetes Control Plane   |  |
|  |                            |  |
|  | API Server                 |  |
|  | etcd                       |  |
|  | Scheduler                  |  |
|  | Controller Manager         |  |
|  +----------------------------+  |
|               |                  |
+---------------|------------------+
                |
                v
        Kubernetes Workloads
                |
                v
        EventFlow Deployment
          desired replicas: 3
                |
                v
          EventFlow Pods