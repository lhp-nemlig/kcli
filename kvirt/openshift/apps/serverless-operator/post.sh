echo """apiVersion: operator.knative.dev/v1alpha1
kind: KnativeEventing
metadata:
  name: knative-eventing
  namespace: {{ namespace }}""" | oc apply -f -
