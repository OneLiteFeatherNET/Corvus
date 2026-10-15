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
    implementation("com.moandjiezana.toml:toml4j:0.7.2") {
        exclude(group = "com.google.code.gson")
    }

    compileOnly("com.velocitypowered:velocity-api:$velocityVersion")
    annotationProcessor("com.velocitypowered:velocity-api:$velocityVersion")
    compileOnly("com.github.retrooper:packetevents-api:$packetEventsVersion")
    compileOnly("io.netty:netty-buffer:$nettyVersion")
    compileOnly("io.netty:netty-transport:$nettyVersion")

    testImplementation("com.velocitypowered:velocity-api:$velocityVersion")
    testImplementation("com.github.retrooper:packetevents-api:$packetEventsVersion")
    testImplementation("io.netty:netty-buffer:$nettyVersion")
    testImplementation("com.google.code.gson:gson:2.14.0")
    testImplementation(platform("org.junit:junit-bom:5.14.4"))
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
    relocate("com.google.protobuf", "net.onelitefeather.corvus.shaded.protobuf")
    relocate("com.moandjiezana.toml", "net.onelitefeather.corvus.shaded.toml")
}

tasks.build { dependsOn(tasks.shadowJar) }
