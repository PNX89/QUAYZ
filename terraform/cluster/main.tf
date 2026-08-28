# Deploying the chart through Terraform, so that `plan` is the deploy diff and a hand edit shows
# up as one.
#
# WHY THIS EXISTS BESIDE helm. helm knows what it installed. It does not know what somebody did
# to the cluster afterwards, and neither does a pipeline that only ever runs `helm upgrade`. A
# plan against a declared configuration is the instrument for the fifth failure in
# src/quayz/failures.py, the one where every health check passes because the cluster is healthy
# and it is the configuration that is no longer what anybody wrote down.
#
# WHAT IT DOES NOT ESTABLISH is in docs/adr/0001, including the honest answer about when Argo CD
# is the right tool instead of this one.

terraform {
  required_version = ">= 1.6"
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 3.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 3.0"
    }
  }
}

variable "kubeconfig" {
  description = "Path to a kubeconfig. No default: a provider that silently picks up whatever context is current is how somebody applies to the wrong cluster."
  type        = string
}

variable "context" {
  description = "The kubeconfig context to use, named rather than inherited, for the same reason."
  type        = string
}

variable "chart_path" {
  description = "Path to charts/deploy-canary, relative to this directory or absolute."
  type        = string
}

variable "replicas" {
  description = "Declared here so a hand edit to the cluster disagrees with something written down."
  type        = number
  default     = 2
}

provider "kubernetes" {
  config_path    = var.kubeconfig
  config_context = var.context
}

provider "helm" {
  kubernetes = {
    config_path    = var.kubeconfig
    config_context = var.context
  }
}

resource "helm_release" "canary" {
  name  = "canary"
  chart = var.chart_path

  # Waits, so an apply that returns has actually deployed something. Without this the apply
  # succeeds the moment the objects are created and a broken deploy looks like a good one.
  wait    = true
  timeout = 180

  set = [
    {
      name  = "replicaCount"
      value = tostring(var.replicas)
    }
  ]
}
