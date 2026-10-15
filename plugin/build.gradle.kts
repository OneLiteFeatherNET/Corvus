plugins {
    `java-library`
    id("com.gradleup.shadow") version "9.6.1"
}

java {
    toolchain { languageVersion = JavaLanguageVersion.of(25) }
}

val velocityVersion = "4.2.0"
val packetEventsVersion = "2.14.0"
val nettyVersion = "4.2.19.Final"

val generateTemplates = tasks.register<Copy>("generateTemplates") {
    val version = project.version.toString()
    inputs.property("version", version)
    from("src/main/templates")
    into(layout.buildDirectory.dir("generated/sources/templates"))
    expand("version" to version)
}

sourceSets.main {
    java.srcDir(generateTemplates.map { it.destinationDir })
}

dependencies {
    implementation(project(":proto-java"))
    // Public API for other plugins: shaded into the plugin jar unrelocated (see shadowJar below).
    implementation(project(":api"))
    implementation("io.nats:jnats:2.26.4")
    implementation("com.moandjiezana.toml:toml4j:0.7.2") {
        exclude(group = "com.google.code.gson")
    }

    compileOnly("com.velocitypowered:velocity-api:$velocityVersion")
    annotationProcessor("com.velocitypowered:velocity-api:$velocityVersion")
    compileOnly("com.github.retrooper:packetevents-api:$packetEventsVersion")
    // Velocity ships Gson (toml4j relies on it too).
    compileOnly("com.google.code.gson:gson:2.14.0")
    compileOnly("io.netty:netty-buffer:$nettyVersion")
    compileOnly("io.netty:netty-transport:$nettyVersion")

    testImplementation("com.velocitypowered:velocity-api:$velocityVersion")
    testImplementation("com.github.retrooper:packetevents-api:$packetEventsVersion")
    testImplementation("io.netty:netty-buffer:$nettyVersion")
    testImplementation("com.google.code.gson:gson:2.14.0")
    testImplementation(platform("org.junit:junit-bom:6.1.3"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

val vocabularyJson = rootProject.file("proto/corvus/capture/v1/vocabulary.json")

tasks.test {
    useJUnitPlatform()
    maxParallelForks = 1
    // The committed vocabulary export is checked against the Java vocabulary. Regenerate it with
    //   ./gradlew :plugin:test --tests '*VocabularyJsonTest' -PupdateVocabulary
    inputs.files(vocabularyJson)
    val pilotConfig = rootProject.file("deploy/pilot/proxy/corvus-config.toml")
    inputs.files(pilotConfig)
    systemProperty("corvus.pilot.config", pilotConfig.absolutePath)
    systemProperty("corvus.vocabulary.json", vocabularyJson.absolutePath)
    systemProperty("corvus.vocabulary.update", providers.gradleProperty("updateVocabulary").isPresent.toString())
}

tasks.shadowJar {
    archiveBaseName = "corvus-velocity"
    archiveClassifier = ""
    mergeServiceFiles()
    relocate("com.google.protobuf", "net.onelitefeather.corvus.shaded.protobuf")
    relocate("com.moandjiezana.toml", "net.onelitefeather.corvus.shaded.toml")
    relocate("io.nats", "net.onelitefeather.corvus.shaded.nats")
    relocate("org.bouncycastle", "net.onelitefeather.corvus.shaded.bouncycastle")
    relocate("org.jspecify", "net.onelitefeather.corvus.shaded.jspecify")
    // net.onelitefeather.corvus.api is deliberately NOT relocated: other plugins compile against it.
}

tasks.build { dependsOn(tasks.shadowJar) }
