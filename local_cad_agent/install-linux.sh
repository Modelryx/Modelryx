#!/usr/bin/env sh
set -eu

install_root="${XDG_DATA_HOME:-$HOME/.local/share}/modelryx/local-cad-agent"
config_root="${XDG_CONFIG_HOME:-$HOME/.config}/Modelryx/LocalCadAgent"
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$install_root/app/preprocessors" "$config_root" "$HOME/.local/share/applications"
prebuilt="$project_dir/artifacts/linux-x64/Modelryx.LocalCad.Agent"
if [ -f "$prebuilt" ]; then
  cp "$prebuilt" "$install_root/app/Modelryx.LocalCad.Agent"
else
  if ! command -v dotnet >/dev/null 2>&1; then
    printf '%s\n' "The prebuilt linux-x64 application is missing. Obtain the release package; .NET is required only to build from source." >&2
    exit 1
  fi
  dotnet publish "$project_dir/Modelryx.LocalCad.Agent/Modelryx.LocalCad.Agent.csproj" \
    -c Release -r linux-x64 --self-contained true \
    -p:PublishSingleFile=true -p:IncludeNativeLibrariesForSelfExtract=true \
    -o "$install_root/app"
fi

# Install native preprocessors separately and register them in ExternalPreprocessors.
if [ ! -f "$config_root/settings.json" ]; then
  printf '{"ExternalPreprocessors": [], "AllowedOrigins": ["http://localhost:5173"]}\n' > "$config_root/settings.json"
fi

desktop_file="$HOME/.local/share/applications/modelryx-local-cad-agent.desktop"
sed "s|__EXECUTABLE__|$install_root/app/Modelryx.LocalCad.Agent|g" \
  "$project_dir/modelryx-local-cad-agent.desktop.in" > "$desktop_file"
chmod +x "$install_root/app/Modelryx.LocalCad.Agent" "$desktop_file"
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$HOME/.local/share/applications"
fi
xdg-mime default modelryx-local-cad-agent.desktop x-scheme-handler/modelryx-cad
printf '%s\n' "Modelryx Local CAD Agent installed: $install_root/app/Modelryx.LocalCad.Agent"
printf '%s\n' "The application is self-contained; the user does not need to install .NET."
