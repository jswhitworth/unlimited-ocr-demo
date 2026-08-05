_runpod_key=$(security find-generic-password -a "$USER" -s "runpod-api-key" -w 2>/dev/null)

if [ -z "$_runpod_key" ]; then
  echo "runpod-api-key not found in Keychain. Set it with:" >&2
  echo '  security add-generic-password -a "$USER" -s "runpod-api-key" -w' >&2
else
  export RUNPOD_API_KEY="$_runpod_key"
fi
unset _runpod_key
