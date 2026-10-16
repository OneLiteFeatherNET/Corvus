package net.onelitefeather.corvus.plugin.config;

import com.google.gson.JsonParseException;
import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;

import java.io.IOException;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.function.Function;

/**
 * Resolves the placeholders of {@code proxy_id} once, when the config is loaded:
 *
 * <ul>
 *   <li>{@code ${cloudnet.service}}: {@code <taskName><nameSplitter><taskServiceId>}, for example {@code Proxy-1}</li>
 *   <li>{@code ${cloudnet.task}}: the CloudNet task name</li>
 *   <li>{@code ${env:NAME}}: the environment variable {@code NAME}</li>
 * </ul>
 *
 * <p>The CloudNet values come from {@code .wrapper/wrapper.json} of the service working directory. Only
 * {@code serviceConfiguration.serviceId} is read, with a streaming reader that skips every other value; the file
 * also holds the node's {@code connectionKey}, which is never kept, logged or put into a message. The result must be
 * a valid NATS subject token (see {@link #validate}).
 */
public final class ProxyIdResolver {

    public static final Path DEFAULT_WRAPPER_FILE = Path.of(".wrapper", "wrapper.json");

    private static final String SERVICE = "cloudnet.service";
    private static final String TASK = "cloudnet.task";
    private static final String ENV_PREFIX = "env:";

    private final Path wrapperFile;
    private final Function<String, String> env;

    public ProxyIdResolver(Path wrapperFile, Function<String, String> env) {
        this.wrapperFile = wrapperFile;
        this.env = env;
    }

    /** Reads {@code .wrapper/wrapper.json} relative to the working directory and the real environment. */
    public static ProxyIdResolver system() {
        return new ProxyIdResolver(DEFAULT_WRAPPER_FILE, System::getenv);
    }

    /** The CloudNet service id fields; the only part of wrapper.json that is ever kept. */
    private record ServiceId(String taskName, String nameSplitter, long taskServiceId) {
    }

    public String resolve(String raw) {
        StringBuilder out = new StringBuilder();
        ServiceId serviceId = null;
        int pos = 0;
        while (pos < raw.length()) {
            int start = raw.indexOf("${", pos);
            if (start < 0) {
                out.append(raw, pos, raw.length());
                break;
            }
            out.append(raw, pos, start);
            int end = raw.indexOf('}', start);
            if (end < 0) {
                throw new ProxyIdException("proxy_id has an unterminated placeholder starting at \""
                        + raw.substring(start) + "\"");
            }
            String placeholder = raw.substring(start, end + 1);
            String name = raw.substring(start + 2, end);
            if (name.startsWith(ENV_PREFIX)) {
                out.append(fromEnv(placeholder, name.substring(ENV_PREFIX.length())));
            } else if (name.equals(SERVICE) || name.equals(TASK)) {
                if (serviceId == null) {
                    serviceId = readServiceId(placeholder);
                }
                out.append(name.equals(TASK) ? serviceId.taskName()
                        : serviceId.taskName() + serviceId.nameSplitter() + serviceId.taskServiceId());
            } else {
                throw new ProxyIdException("proxy_id has an unknown placeholder " + placeholder + " (known: ${"
                        + SERVICE + "}, ${" + TASK + "}, ${env:NAME})");
            }
            pos = end + 1;
        }
        String id = out.toString();
        validate(id, raw);
        return id;
    }

    private String fromEnv(String placeholder, String variable) {
        String value = variable.isEmpty() ? null : env.apply(variable);
        if (value == null || value.isBlank()) {
            throw new ProxyIdException("proxy_id placeholder " + placeholder + " cannot be resolved: the environment "
                    + "variable is not set or empty");
        }
        return value.trim();
    }

    private ServiceId readServiceId(String placeholder) {
        String unreadable = "proxy_id placeholder " + placeholder + " cannot be resolved: ";
        if (!Files.isRegularFile(wrapperFile)) {
            throw new ProxyIdException(unreadable + "CloudNet wrapper file " + wrapperFile + " does not exist (is "
                    + "this proxy a CloudNet service?)");
        }
        try (Reader in = Files.newBufferedReader(wrapperFile, StandardCharsets.UTF_8);
             JsonReader reader = new JsonReader(in)) {
            ServiceId found = readServiceConfiguration(reader);
            if (found == null) {
                throw new ProxyIdException(unreadable + wrapperFile + " has no usable serviceConfiguration.serviceId");
            }
            return found;
        } catch (IOException | JsonParseException | IllegalStateException e) {
            // Deliberately without the cause: parser messages may quote parts of the file.
            throw new ProxyIdException(unreadable + wrapperFile + " could not be read as CloudNet wrapper JSON");
        }
    }

    /** Walks the top-level object and skips everything except serviceConfiguration.serviceId. */
    private static ServiceId readServiceConfiguration(JsonReader reader) throws IOException {
        ServiceId result = null;
        if (reader.peek() != JsonToken.BEGIN_OBJECT) {
            return null;
        }
        reader.beginObject();
        while (reader.hasNext()) {
            if (reader.nextName().equals("serviceConfiguration") && reader.peek() == JsonToken.BEGIN_OBJECT) {
                reader.beginObject();
                while (reader.hasNext()) {
                    if (reader.nextName().equals("serviceId") && reader.peek() == JsonToken.BEGIN_OBJECT) {
                        result = readServiceId(reader);
                    } else {
                        reader.skipValue();
                    }
                }
                reader.endObject();
            } else {
                reader.skipValue();
            }
        }
        reader.endObject();
        return result;
    }

    private static ServiceId readServiceId(JsonReader reader) throws IOException {
        String task = null;
        String splitter = null;
        Long number = null;
        reader.beginObject();
        while (reader.hasNext()) {
            String field = reader.nextName();
            if (field.equals("taskName") && reader.peek() == JsonToken.STRING) {
                task = reader.nextString();
            } else if (field.equals("nameSplitter") && reader.peek() == JsonToken.STRING) {
                splitter = reader.nextString();
            } else if (field.equals("taskServiceId") && reader.peek() == JsonToken.NUMBER) {
                number = reader.nextLong();
            } else {
                reader.skipValue();
            }
        }
        reader.endObject();
        if (task == null || task.isBlank() || splitter == null || number == null) {
            return null;
        }
        return new ServiceId(task, splitter, number);
    }

    /** A usable proxy_id is one NATS subject token: not empty, no {@code .}, {@code *}, {@code >}, whitespace. */
    static void validate(String id, String raw) {
        if (id.isEmpty()) {
            throw new ProxyIdException("proxy_id must not be empty");
        }
        for (int i = 0; i < id.length(); i++) {
            char c = id.charAt(i);
            if (c == '.' || c == '*' || c == '>' || Character.isWhitespace(c) || Character.isISOControl(c)) {
                throw new ProxyIdException("proxy_id \"" + raw + "\" does not resolve to a valid NATS subject token: "
                        + "'.', '*', '>', whitespace and control characters are not allowed (it is part of "
                        + "corvus.capture.v1.<proxy_id>.<partition>)");
            }
        }
    }
}
