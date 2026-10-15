plugins {
    `java-library`
    id("com.google.protobuf") version "0.10.0"
}

java {
    toolchain { languageVersion = JavaLanguageVersion.of(25) }
}

val protobufVersion = "4.36.2"

dependencies {
    api("com.google.protobuf:protobuf-java:$protobufVersion")
}

sourceSets {
    main {
        proto { srcDir("${rootProject.projectDir}/proto") }
    }
}

protobuf {
    protoc { artifact = "com.google.protobuf:protoc:$protobufVersion" }
}
