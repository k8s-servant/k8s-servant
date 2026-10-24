# Changelog

## [0.4.0](https://github.com/k8s-servant/k8s-servant/compare/v0.3.1...v0.4.0) (2026-10-24)


### ⚠ BREAKING CHANGES

* **chart:** no default model provider ([#75](https://github.com/k8s-servant/k8s-servant/issues/75))

### Features

* **chart:** no default model provider ([#75](https://github.com/k8s-servant/k8s-servant/issues/75)) ([5810c7e](https://github.com/k8s-servant/k8s-servant/commit/5810c7e029eb1849fdfd6d57976a8915c061a95c))
* **onboard:** discover cluster facts with evidence ([#35](https://github.com/k8s-servant/k8s-servant/issues/35)) ([#59](https://github.com/k8s-servant/k8s-servant/issues/59)) ([b8785c2](https://github.com/k8s-servant/k8s-servant/commit/b8785c2ba52ee5d7a6d273d42df1e6fe0ef9ad85))
* **onboard:** re-scan before the morning report and show what changed ([#35](https://github.com/k8s-servant/k8s-servant/issues/35)) ([#62](https://github.com/k8s-servant/k8s-servant/issues/62)) ([55f6770](https://github.com/k8s-servant/k8s-servant/commit/55f6770d76802ea59bf8583a7589cf3286394784))
* **onboard:** the onboard command writes the draft values files ([#35](https://github.com/k8s-servant/k8s-servant/issues/35)) ([#61](https://github.com/k8s-servant/k8s-servant/issues/61)) ([4b764f3](https://github.com/k8s-servant/k8s-servant/commit/4b764f3284543226fedc98f193b94891f195786e))
* publish the CLI to PyPI ([#65](https://github.com/k8s-servant/k8s-servant/issues/65)) ([1c45989](https://github.com/k8s-servant/k8s-servant/commit/1c45989967da0afcdb9af36acc12c7f3a88651b5))
* **scripts:** make the Key Vault optional in setup-workload-identity.sh ([#72](https://github.com/k8s-servant/k8s-servant/issues/72)) ([ff07430](https://github.com/k8s-servant/k8s-servant/commit/ff0743028fefc5ca7a6fa47564754a338496561b))


### Bug fixes

* **onboard:** write settings.env next to the values files ([#35](https://github.com/k8s-servant/k8s-servant/issues/35)) ([#63](https://github.com/k8s-servant/k8s-servant/issues/63)) ([f51867e](https://github.com/k8s-servant/k8s-servant/commit/f51867e4a979f99614d52450f0cc8d03a4d48fe3))


### Documentation

* add badges to the PyPI page ([#69](https://github.com/k8s-servant/k8s-servant/issues/69)) ([5ae907f](https://github.com/k8s-servant/k8s-servant/commit/5ae907fc2e528a2e10bd4d367b2c009f8b4f0614))
* add choosing the model to the deployment steps ([#74](https://github.com/k8s-servant/k8s-servant/issues/74)) ([5ecb047](https://github.com/k8s-servant/k8s-servant/commit/5ecb0477061831e63f36e719c5dd5b30cb958fb2))
* call the deployment guide 'Deploying to any cluster' ([#80](https://github.com/k8s-servant/k8s-servant/issues/80)) ([ea4dd65](https://github.com/k8s-servant/k8s-servant/commit/ea4dd65b8258ea2847c450abc02095423019f1e5))
* explain how to add a component to the onboard catalog ([#66](https://github.com/k8s-servant/k8s-servant/issues/66)) ([01bc147](https://github.com/k8s-servant/k8s-servant/commit/01bc1470fbbf07b19fed7aa339c97d3ae0a4b10b))
* first run with k8s-servant onboard ([#35](https://github.com/k8s-servant/k8s-servant/issues/35)) ([#64](https://github.com/k8s-servant/k8s-servant/issues/64)) ([70cf3d7](https://github.com/k8s-servant/k8s-servant/commit/70cf3d7c211e0964c6763af01ba2cc5fab3fde53))
* give AKS its own complete install steps ([#73](https://github.com/k8s-servant/k8s-servant/issues/73)) ([08977c4](https://github.com/k8s-servant/k8s-servant/commit/08977c40dc92804ed0a6c0bdcac452370d5fc68c))
* install with Argo CD as well as Flux ([#78](https://github.com/k8s-servant/k8s-servant/issues/78)) ([1686aab](https://github.com/k8s-servant/k8s-servant/commit/1686aaba8320e0978eea399439822082b0512cfa))
* make the deployment guide provider-neutral ([#71](https://github.com/k8s-servant/k8s-servant/issues/71)) ([2e3ccd5](https://github.com/k8s-servant/k8s-servant/commit/2e3ccd5c97e5bede2e9ef3ccad4a57f7119798bc))
* move the onboard catalog guide out of the user guide ([#68](https://github.com/k8s-servant/k8s-servant/issues/68)) ([0b24376](https://github.com/k8s-servant/k8s-servant/commit/0b243762295edd5b2fb38b4c03eaea2fafb5e6c5))
* recommend a secret tool over hand-made Secrets for long-running installs ([#77](https://github.com/k8s-servant/k8s-servant/issues/77)) ([aafe9bf](https://github.com/k8s-servant/k8s-servant/commit/aafe9bfb7e8802f4e0f42089641b8d89c44cb0a1))
* rename 'First run' to 'Describe your platform', drop 'Other clusters' ([#83](https://github.com/k8s-servant/k8s-servant/issues/83)) ([380fbcf](https://github.com/k8s-servant/k8s-servant/commit/380fbcf8ba19723cf2a75edd754a93f270a782a5))
* show all sections in the left sidebar instead of top tabs ([#81](https://github.com/k8s-servant/k8s-servant/issues/81)) ([61fb943](https://github.com/k8s-servant/k8s-servant/commit/61fb9431f182754a7c18dd306c29959e6bd2ba97))
* show Foundry with an API key, with Workload Identity as the recommended way ([#76](https://github.com/k8s-servant/k8s-servant/issues/76)) ([9003a74](https://github.com/k8s-servant/k8s-servant/commit/9003a74aa438f9a6e65d4e5d4aac555af655d76c))
* show the full sidebar on every page, home included ([#82](https://github.com/k8s-servant/k8s-servant/issues/82)) ([394705d](https://github.com/k8s-servant/k8s-servant/commit/394705d685195d1aec480c531ce3ba0de623f9d4))

## [0.3.1](https://github.com/k8s-servant/k8s-servant/compare/v0.3.0...v0.3.1) (2026-10-20)


### Documentation

* add a code walkthrough for contributors ([#49](https://github.com/k8s-servant/k8s-servant/issues/49)) ([ae09366](https://github.com/k8s-servant/k8s-servant/commit/ae09366cc4763fa71ddd263bb61cccd566cd55d5))
* add running from a mirror ([#45](https://github.com/k8s-servant/k8s-servant/issues/45)) ([f6b20bc](https://github.com/k8s-servant/k8s-servant/commit/f6b20bc33a1c7e4903a1c7e70383215f861cb4a8))
* add the agent team idea to the roadmap ([#48](https://github.com/k8s-servant/k8s-servant/issues/48)) ([f66e873](https://github.com/k8s-servant/k8s-servant/commit/f66e873963697f7c8ddaeddbfc824d87f9de1c17))
* add the command centre to the vision and roadmap ([#38](https://github.com/k8s-servant/k8s-servant/issues/38)) ([#40](https://github.com/k8s-servant/k8s-servant/issues/40)) ([bdbd50c](https://github.com/k8s-servant/k8s-servant/commit/bdbd50cf7a154bd0feaa62a57b56646813223fda))
* call it Microsoft Foundry everywhere ([#58](https://github.com/k8s-servant/k8s-servant/issues/58)) ([3f79451](https://github.com/k8s-servant/k8s-servant/commit/3f794517435d1c2746e194fc5660ead05d2318fa))
* drop the planned comparison with HolmesGPT ([#56](https://github.com/k8s-servant/k8s-servant/issues/56)) ([9f45327](https://github.com/k8s-servant/k8s-servant/commit/9f45327ad73352a7e1913bfa37de0b80adb82e4c))
* give each model provider its own page and regroup the site navigation ([#54](https://github.com/k8s-servant/k8s-servant/issues/54)) ([a8d4e81](https://github.com/k8s-servant/k8s-servant/commit/a8d4e819854ec3fdb5b39b35fc435bcb41c53b10))
* make Azure AI Foundry a clickable section and fix lists on the site ([#55](https://github.com/k8s-servant/k8s-servant/issues/55)) ([9d79838](https://github.com/k8s-servant/k8s-servant/commit/9d798384cd8d3fa3a8765640b450b3748b87f4fa))
* make the remaining docs provider-neutral ([#51](https://github.com/k8s-servant/k8s-servant/issues/51)) ([5ba22cc](https://github.com/k8s-servant/k8s-servant/commit/5ba22ccb879ee99c83e16d6c1bc7f8cfdf84813d))
* move assistant guidance to AGENTS.md ([#46](https://github.com/k8s-servant/k8s-servant/issues/46)) ([5817bc5](https://github.com/k8s-servant/k8s-servant/commit/5817bc56f1675201bd6210984a12ecbeca86249c))
* move the reference sections from the README into docs/ ([#52](https://github.com/k8s-servant/k8s-servant/issues/52)) ([b2f1d75](https://github.com/k8s-servant/k8s-servant/commit/b2f1d757b002352b2b038c91b3490d6dbf88e316))
* publish the documentation site on k8s-servant.dev ([#53](https://github.com/k8s-servant/k8s-servant/issues/53)) ([ae8323d](https://github.com/k8s-servant/k8s-servant/commit/ae8323d38ae48f90afb8b799fd7f0ea714fdb538))
* replace the logo with a flat dome robot ([#44](https://github.com/k8s-servant/k8s-servant/issues/44)) ([100d161](https://github.com/k8s-servant/k8s-servant/commit/100d16141a53b1fc0ff385fb278ef3ac1c39e8ce))
* rewrite the README quickstart around a kind demo ([#50](https://github.com/k8s-servant/k8s-servant/issues/50)) ([6111249](https://github.com/k8s-servant/k8s-servant/commit/61112499e2257fa9370d76cbea30740d5edc6a2d))
* show Foundry's pages as a nested, collapsible group in the sidebar ([#57](https://github.com/k8s-servant/k8s-servant/issues/57)) ([28776f9](https://github.com/k8s-servant/k8s-servant/commit/28776f920417d3e0f49d19b1235163782757924f))

## [0.3.0](https://github.com/k8s-servant/k8s-servant/compare/v0.2.0...v0.3.0) (2026-10-18)


### ⚠ BREAKING CHANGES

* support multiple model providers ([#2](https://github.com/k8s-servant/k8s-servant/issues/2)) (#39)

### Features

* support multiple model providers ([#2](https://github.com/k8s-servant/k8s-servant/issues/2)) ([#39](https://github.com/k8s-servant/k8s-servant/issues/39)) ([3eb8d3d](https://github.com/k8s-servant/k8s-servant/commit/3eb8d3df9c5c31d902349e54696356c3d94dc9ad))


### Documentation

* add an early-stage warning and refresh the README ([#26](https://github.com/k8s-servant/k8s-servant/issues/26)) ([30f0b4e](https://github.com/k8s-servant/k8s-servant/commit/30f0b4e62e9821c00a5138105289372f0bab2287))
* add the initial scan and a configurable persona to the vision ([#37](https://github.com/k8s-servant/k8s-servant/issues/37)) ([3aefdd7](https://github.com/k8s-servant/k8s-servant/commit/3aefdd7dde324dfd28c8d07b9f1529fdb8fb2092))
* add the project vision and reorder the roadmap around it ([#33](https://github.com/k8s-servant/k8s-servant/issues/33)) ([c450382](https://github.com/k8s-servant/k8s-servant/commit/c450382bb310b10b9c9beefdb387db92e0fc52a5))
* explain why maintainers need public org membership for [@claude](https://github.com/claude) ([#23](https://github.com/k8s-servant/k8s-servant/issues/23)) ([7648868](https://github.com/k8s-servant/k8s-servant/commit/7648868f3402ef21cda5debcc33be688e9756523))
* place the documentation site between neutral docs and the Teams bot ([#34](https://github.com/k8s-servant/k8s-servant/issues/34)) ([b2c9c97](https://github.com/k8s-servant/k8s-servant/commit/b2c9c97b380e9e11912b74162f94a3c045bdfc9f))

## [0.2.0](https://github.com/k8s-servant/k8s-servant/compare/v0.1.0...v0.2.0) (2026-10-17)


### ⚠ BREAKING CHANGES

* **chart:** bring your own Secrets, identity and extras ([#19](https://github.com/k8s-servant/k8s-servant/issues/19))

### Features

* **chart:** bring your own Secrets, identity and extras ([#19](https://github.com/k8s-servant/k8s-servant/issues/19)) ([cd07c5f](https://github.com/k8s-servant/k8s-servant/commit/cd07c5f74a576925227dcce173070f0ddb4a59bf))

## 0.1.0 (2026-10-17)


### Features

* initial import of k8s-servant ([#1](https://github.com/k8s-servant/k8s-servant/issues/1)) ([8ae5151](https://github.com/k8s-servant/k8s-servant/commit/8ae515120bc27fe42df5ce315c517da2275f7d37))


### Documentation

* explain how to verify signed releases ([#16](https://github.com/k8s-servant/k8s-servant/issues/16)) ([07d7998](https://github.com/k8s-servant/k8s-servant/commit/07d79980ab8c4d4dc65c9f70784e897dcae8907a))
