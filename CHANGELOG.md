# Changelog

## [0.5.0](https://github.com/OneLiteFeatherNET/Corvus/compare/v0.4.0...v0.5.0) (2026-10-16)


### Features

* default to 20 s windows with 10 s stride and 2048 packets ([#89](https://github.com/OneLiteFeatherNET/Corvus/issues/89)) ([5eb1017](https://github.com/OneLiteFeatherNET/Corvus/commit/5eb1017c403adf54f86264cbe0f207702b587195))
* **ml:** add raw-byte model variants for the tokenizer ablation ([#87](https://github.com/OneLiteFeatherNET/Corvus/issues/87)) ([1660535](https://github.com/OneLiteFeatherNET/Corvus/commit/1660535c29029ba95dc3b84e06f35fa34c776dfc))
* **ml:** find the learning rate with a range test ([#85](https://github.com/OneLiteFeatherNET/Corvus/issues/85)) ([5f8dbbf](https://github.com/OneLiteFeatherNET/Corvus/commit/5f8dbbfcdcb868d1b14a036f4d39989f1f062419))
* treat scoring latency as throughput instead of a 10 ms budget ([#88](https://github.com/OneLiteFeatherNET/Corvus/issues/88)) ([2c98257](https://github.com/OneLiteFeatherNET/Corvus/commit/2c9825700576f892fb56b0f680bca39996bb719e))

## [0.4.0](https://github.com/OneLiteFeatherNET/Corvus/compare/v0.3.2...v0.4.0) (2026-10-16)


### Features

* **plugin:** resolve proxy_id from the cloudnet service ([#82](https://github.com/OneLiteFeatherNET/Corvus/issues/82)) ([336edce](https://github.com/OneLiteFeatherNET/Corvus/commit/336edce9a9772091987e84c8093a85d6532afdec))
* **ui:** list snapshot sessions with label action ([#83](https://github.com/OneLiteFeatherNET/Corvus/issues/83)) ([72ecbf3](https://github.com/OneLiteFeatherNET/Corvus/commit/72ecbf340d64c3732f7a4694f4af6085bbe02494))

## [0.3.2](https://github.com/OneLiteFeatherNET/Corvus/compare/v0.3.1...v0.3.2) (2026-10-16)


### Bug Fixes

* **services:** split liveness from readiness and shut down cleanly ([#80](https://github.com/OneLiteFeatherNET/Corvus/issues/80)) ([f0a8be0](https://github.com/OneLiteFeatherNET/Corvus/commit/f0a8be04c236ce0ff18212f7bf5dc2df3d216c96))

## [0.3.1](https://github.com/OneLiteFeatherNET/Corvus/compare/v0.3.0...v0.3.1) (2026-10-16)


### Bug Fixes

* **services:** start ml jobs with the corvus-job entry point ([#78](https://github.com/OneLiteFeatherNET/Corvus/issues/78)) ([7b97289](https://github.com/OneLiteFeatherNET/Corvus/commit/7b972894719b6ba51dd37f55ac1df42b5fb6cd8c))

## [0.3.0](https://github.com/OneLiteFeatherNET/Corvus/compare/v0.2.0...v0.3.0) (2026-10-16)


### Features

* **plugin:** map cloudnet service names to their task's game context ([#77](https://github.com/OneLiteFeatherNET/Corvus/issues/77)) ([3ae0671](https://github.com/OneLiteFeatherNET/Corvus/commit/3ae067188ed243b1580b12e2b6cc7c634a1aab05))
* **plugin:** trust an extra ca bundle for nats and the control plane ([#75](https://github.com/OneLiteFeatherNET/Corvus/issues/75)) ([8e0e4ce](https://github.com/OneLiteFeatherNET/Corvus/commit/8e0e4ce29ef1bb5f3ee950eb1cf143a1c4401e0a))

## [0.2.0](https://github.com/OneLiteFeatherNET/Corvus/compare/v0.1.1...v0.2.0) (2026-10-16)


### Features

* **ui:** rebuild the interface with tailwind css and material 3 ([#72](https://github.com/OneLiteFeatherNET/Corvus/issues/72)) ([985adee](https://github.com/OneLiteFeatherNET/Corvus/commit/985adee53997d3077b35e000983b744b221f9dbd))

## [0.1.1](https://github.com/OneLiteFeatherNET/Corvus/compare/v0.1.0...v0.1.1) (2026-10-16)


### Bug Fixes

* **services:** install nkeys for nats creds authentication ([#69](https://github.com/OneLiteFeatherNET/Corvus/issues/69)) ([7977ce4](https://github.com/OneLiteFeatherNET/Corvus/commit/7977ce4c54299637ff954b1600c94f5c2cb41849))
* **ui:** make the nginx entrypoint script executable ([#70](https://github.com/OneLiteFeatherNET/Corvus/issues/70)) ([5bda246](https://github.com/OneLiteFeatherNET/Corvus/commit/5bda246b68916c3060103c8f1ae24562bce8fb83))

## 0.1.0 (2026-10-15)


### Features

* **deploy:** add helm chart ([#34](https://github.com/OneLiteFeatherNET/Corvus/issues/34)) ([1b352f9](https://github.com/OneLiteFeatherNET/Corvus/commit/1b352f922a8f30fc35b1289a0b0ed57c09301137))
* **ml:** add model evaluation harness with baseline candidates ([#18](https://github.com/OneLiteFeatherNET/Corvus/issues/18)) ([3abac1e](https://github.com/OneLiteFeatherNET/Corvus/commit/3abac1ed620a9570f9b6d9f219bfbbed588059f8))
* **ml:** add pilot capture analyzer ([5a749f2](https://github.com/OneLiteFeatherNET/Corvus/commit/5a749f2cc04476c3bc0b609dad995ce994611fa2))
* **ml:** build context windows from lake events ([#5](https://github.com/OneLiteFeatherNET/Corvus/issues/5)) ([a95ab7f](https://github.com/OneLiteFeatherNET/Corvus/commit/a95ab7f528c991e958a176f9f61035f89f50b328))
* **ml:** check payloads against the plugin vocabulary ([0d9394c](https://github.com/OneLiteFeatherNET/Corvus/commit/0d9394c866c5aed191b06c52f8f6b3525dab2a5a))
* **ml:** register job artefacts and read raw file lists ([#56](https://github.com/OneLiteFeatherNET/Corvus/issues/56)) ([317d002](https://github.com/OneLiteFeatherNET/Corvus/commit/317d002aa8f1179d5e8e5f62c2da09c2741e45d7))
* **ml:** report share of truncated payloads ([9338871](https://github.com/OneLiteFeatherNET/Corvus/commit/93388710ec0bc203f04cf30ddf50d859b8408b58))
* **ml:** run feature, evaluation and training jobs from snapshots ([#46](https://github.com/OneLiteFeatherNET/Corvus/issues/46)) ([8a7dce6](https://github.com/OneLiteFeatherNET/Corvus/commit/8a7dce62dbe4d159d097ba2c205c7cefa74c2e4b))
* **ml:** tokenize raw packet bytes for a hierarchical model ([#51](https://github.com/OneLiteFeatherNET/Corvus/issues/51)) ([4510208](https://github.com/OneLiteFeatherNET/Corvus/commit/451020841c7bd7b7e211326922d91b5214002b61))
* **plugin:** capture packets passively via packetevents ([e811501](https://github.com/OneLiteFeatherNET/Corvus/commit/e811501e2e402e66e19fb7fee23e8beef4fcf429))
* **plugin:** export packet vocabulary as json ([c90868d](https://github.com/OneLiteFeatherNET/Corvus/commit/c90868dc3fa20f931bdd845e9835674060fba6ff))
* **plugin:** expose player scores through a velocity api ([#23](https://github.com/OneLiteFeatherNET/Corvus/issues/23)) ([975b008](https://github.com/OneLiteFeatherNET/Corvus/commit/975b008d0891778490423cbe602c11b52655a16d))
* **plugin:** publish captures to nats jetstream ([#4](https://github.com/OneLiteFeatherNET/Corvus/issues/4)) ([1016cb4](https://github.com/OneLiteFeatherNET/Corvus/commit/1016cb414f402cb5e3bdfc97e3ca81c1f2e79dc2))
* **plugin:** record original payload size ([fa1fd72](https://github.com/OneLiteFeatherNET/Corvus/commit/fa1fd72f4c3cb0589452384ea8207802ee20f049))
* **plugin:** restrict raw payloads by direction and packet type ([a0beb8d](https://github.com/OneLiteFeatherNET/Corvus/commit/a0beb8d5818d8ead2e502466ac1fb03504fbb797))
* **plugin:** sync the blocklist from the control plane ([#40](https://github.com/OneLiteFeatherNET/Corvus/issues/40)) ([7f109b1](https://github.com/OneLiteFeatherNET/Corvus/commit/7f109b19b330c2a2e1470ee8b3c1bcb9de565524))
* **proto:** record original payload size ([e8f70a7](https://github.com/OneLiteFeatherNET/Corvus/commit/e8f70a7bccaa37d1cf516a1b0f0ee75efd599958))
* record entity spawn positions for attack geometry ([#17](https://github.com/OneLiteFeatherNET/Corvus/issues/17)) ([263c099](https://github.com/OneLiteFeatherNET/Corvus/commit/263c0991ce972e5be7776ace7ba69abcb84dde2b))
* **services:** add control plane api ([#24](https://github.com/OneLiteFeatherNET/Corvus/issues/24)) ([c3db3b5](https://github.com/OneLiteFeatherNET/Corvus/commit/c3db3b5777c3c242b7353848e8944ea7049a8da4))
* **services:** add ingest service with parquet lake and catalog ([#6](https://github.com/OneLiteFeatherNET/Corvus/issues/6)) ([efd6a74](https://github.com/OneLiteFeatherNET/Corvus/commit/efd6a7413b75975f459ea9305af75cf0910abb83))
* **services:** add online scoring service ([#38](https://github.com/OneLiteFeatherNET/Corvus/issues/38)) ([0df2a10](https://github.com/OneLiteFeatherNET/Corvus/commit/0df2a10ca7d9edc9d8c84fd151d109cc2b754dd7))
* **services:** compact lake files after player erasure ([#41](https://github.com/OneLiteFeatherNET/Corvus/issues/41)) ([f3eae4c](https://github.com/OneLiteFeatherNET/Corvus/commit/f3eae4cad8c100f556a581dd022cb4b598e33566))
* **services:** extend the control plane api for the management ui ([#44](https://github.com/OneLiteFeatherNET/Corvus/issues/44)) ([3ebb0bd](https://github.com/OneLiteFeatherNET/Corvus/commit/3ebb0bdbf240c7c5cfb5c1f245fa0abd1e169e97))
* **services:** score raw-byte models online ([#53](https://github.com/OneLiteFeatherNET/Corvus/issues/53)) ([3c1bb27](https://github.com/OneLiteFeatherNET/Corvus/commit/3c1bb278ecbda310c365d6a2bdacbc087bbb9f78))
* **services:** support nats credentials and stream replicas ([#48](https://github.com/OneLiteFeatherNET/Corvus/issues/48)) ([7ee6d2b](https://github.com/OneLiteFeatherNET/Corvus/commit/7ee6d2b83e95ea4403d3bda661f5080689cb6b15))
* **ui:** add material 3 management interface ([#35](https://github.com/OneLiteFeatherNET/Corvus/issues/35)) ([34fe0ea](https://github.com/OneLiteFeatherNET/Corvus/commit/34fe0ea109c133aa9b360612d093a0aa25e80ba3))
* **ui:** use the extended control plane api ([#52](https://github.com/OneLiteFeatherNET/Corvus/issues/52)) ([e17e243](https://github.com/OneLiteFeatherNET/Corvus/commit/e17e243fc0e23f9b1a074840ae5c781c568d6a2b))


### Bug Fixes

* **deploy:** align pilot config with the plugin config loader ([ed204b1](https://github.com/OneLiteFeatherNET/Corvus/commit/ed204b10426ca43ab9ed9123c1b637cd7158f659))
* **deploy:** align the helm ui config with the ui config format ([#45](https://github.com/OneLiteFeatherNET/Corvus/issues/45)) ([39eb64f](https://github.com/OneLiteFeatherNET/Corvus/commit/39eb64f9392d543beb84238a33047b2dcb7283ed))
* **deps:** update dependency com.google.code.gson:gson to v2.14.0 ([#9](https://github.com/OneLiteFeatherNET/Corvus/issues/9)) ([1b27a9e](https://github.com/OneLiteFeatherNET/Corvus/commit/1b27a9e6607498a5394d95db7f3c6e260e1f96ce))
* **deps:** update dependency org.junit:junit-bom to v6 ([#10](https://github.com/OneLiteFeatherNET/Corvus/issues/10)) ([2f763ea](https://github.com/OneLiteFeatherNET/Corvus/commit/2f763eac6095d0e77e34f7977e9507f373dff765))
* **deps:** update dependency org.junit:junit-bom to v6.0.1 ([#16](https://github.com/OneLiteFeatherNET/Corvus/issues/16)) ([39d88e9](https://github.com/OneLiteFeatherNET/Corvus/commit/39d88e925e0bbaf7c76f6f7654d84e9697770123))
* **deps:** update dependency org.junit:junit-bom to v6.0.2 ([#19](https://github.com/OneLiteFeatherNET/Corvus/issues/19)) ([2344d19](https://github.com/OneLiteFeatherNET/Corvus/commit/2344d19f805e04beceb8f98118f8fcd98a0a30b2))
* **deps:** update dependency org.junit:junit-bom to v6.0.3 ([#22](https://github.com/OneLiteFeatherNET/Corvus/issues/22)) ([30c9239](https://github.com/OneLiteFeatherNET/Corvus/commit/30c9239c093655541c23353c189b1c4638d9681f))
* **deps:** update dependency org.junit:junit-bom to v6.0.3 ([#26](https://github.com/OneLiteFeatherNET/Corvus/issues/26)) ([f0bad39](https://github.com/OneLiteFeatherNET/Corvus/commit/f0bad396f4e6780e27b29b1c6e6029bc050ba179))
* **deps:** update dependency org.junit:junit-bom to v6.1.0 ([#25](https://github.com/OneLiteFeatherNET/Corvus/issues/25)) ([45ca246](https://github.com/OneLiteFeatherNET/Corvus/commit/45ca2461236e0bb46e6bbabffeb2dd586ee0a561))
* **deps:** update dependency org.junit:junit-bom to v6.1.0 ([#37](https://github.com/OneLiteFeatherNET/Corvus/issues/37)) ([02f181e](https://github.com/OneLiteFeatherNET/Corvus/commit/02f181e6884363674e11c5ae8bf952981af442fd))
* **deps:** update dependency org.junit:junit-bom to v6.1.1 ([#29](https://github.com/OneLiteFeatherNET/Corvus/issues/29)) ([1aafb4e](https://github.com/OneLiteFeatherNET/Corvus/commit/1aafb4ebacede54589667f7ae13dd2b7caf8e650))
* **deps:** update dependency org.junit:junit-bom to v6.1.1 ([#43](https://github.com/OneLiteFeatherNET/Corvus/issues/43)) ([7c2b3d0](https://github.com/OneLiteFeatherNET/Corvus/commit/7c2b3d02912764960ab308c8ca4af53b607906d3))
* **deps:** update dependency org.junit:junit-bom to v6.1.2 ([#31](https://github.com/OneLiteFeatherNET/Corvus/issues/31)) ([a546f14](https://github.com/OneLiteFeatherNET/Corvus/commit/a546f14f9a09970d3740c94b291bcc9505325878))
* **deps:** update dependency org.junit:junit-bom to v6.1.2 ([#50](https://github.com/OneLiteFeatherNET/Corvus/issues/50)) ([0fcf044](https://github.com/OneLiteFeatherNET/Corvus/commit/0fcf044d449b5e8a91d2b339ccd1d1af43a3ae70))
* **deps:** update dependency org.junit:junit-bom to v6.1.3 ([#32](https://github.com/OneLiteFeatherNET/Corvus/issues/32)) ([237d74c](https://github.com/OneLiteFeatherNET/Corvus/commit/237d74ce17938de84d1c66632523a1fdd112154f))
* **services:** cover raw-byte training data in erasure and retention ([#54](https://github.com/OneLiteFeatherNET/Corvus/issues/54)) ([ae7b082](https://github.com/OneLiteFeatherNET/Corvus/commit/ae7b08247ad4d3a1bd5abea3bbf4e6eab6c56f1c))
* **services:** hide shadow scores from staff ([#55](https://github.com/OneLiteFeatherNET/Corvus/issues/55)) ([2d17e4f](https://github.com/OneLiteFeatherNET/Corvus/commit/2d17e4f6dac7503bf3943578e4abdf0762e251cd))
* **services:** read calibrated model outputs and fix venv entrypoints ([#49](https://github.com/OneLiteFeatherNET/Corvus/issues/49)) ([daa078c](https://github.com/OneLiteFeatherNET/Corvus/commit/daa078cae682538cfd0172aceeee03490996c663))


### Code Refactoring

* **ml:** share an incremental featurizer between training and serving ([#30](https://github.com/OneLiteFeatherNET/Corvus/issues/30)) ([15f6dbb](https://github.com/OneLiteFeatherNET/Corvus/commit/15f6dbba8d26f65d5526fe0753ff06c158228519))
