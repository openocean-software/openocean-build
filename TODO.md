# TODO

- Recombine `moos-ivp-helm` into the `moos-ivp` project once a MOOS-IvP
  release includes moos-ivp/moos-ivp#114 (installable CMake packages) and
  #147 (`IVP_BUILD_HELM_LIBS_ONLY`): build `libivp-helm*` from a second
  configure in moos-ivp's `debian/rules`, as a release rather than
  continuous snapshots of GobySoft/moos-ivp's `ivp-without-moos` branch,
  and retire `projects/moos-ivp-helm`.
