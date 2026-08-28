(() => {
  const typeSelect = document.getElementById("tipo_documento");
  const daysInput = document.getElementById("dias_afastamento");
  const cpfInput = document.getElementById("cpf");
  const cidInput = document.getElementById("cid");
  const dateInput = document.getElementById("data_atestado");

  const setFeedback = (input, feedback, valid, message) => {
    input.style.borderColor = valid ? "var(--color-success)" : "var(--color-error)";
    feedback.style.color = valid ? "var(--color-success-dark)" : "var(--color-error)";
    feedback.textContent = message;
    input.setAttribute("aria-invalid", String(!valid));
  };

  const validCPF = (value) => {
    const digits = value.replace(/\D/g, "");
    if (digits.length !== 11 || /^(\d)\1{10}$/.test(digits)) return false;
    for (let size = 9; size <= 10; size += 1) {
      let total = 0;
      for (let index = 0; index < size; index += 1) total += Number(digits[index]) * (size + 1 - index);
      let check = (total * 10) % 11;
      if (check === 10) check = 0;
      if (check !== Number(digits[size])) return false;
    }
    return true;
  };

  const validateCPF = () => {
    const digits = cpfInput.value.replace(/\D/g, "").slice(0, 11);
    cpfInput.value = digits.replace(/(\d{3})(\d)/, "$1.$2").replace(/(\d{3})(\d)/, "$1.$2").replace(/(\d{3})(\d{1,2})$/, "$1-$2");
    setFeedback(cpfInput, document.getElementById("cpf-feedback"), validCPF(digits), digits.length < 11 ? "CPF incompleto." : validCPF(digits) ? "CPF válido." : "CPF inválido. Confira os números.");
  };

  const validateCID = () => {
    const compact = cidInput.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 5);
    cidInput.value = compact.slice(0, 3) + (compact.length > 3 ? `.${compact.slice(3)}` : "");
    const valid = !compact || /^[A-Z]\d{2}[A-Z0-9]{0,2}$/.test(compact);
    setFeedback(cidInput, document.getElementById("cid-feedback"), valid, valid ? (compact ? "CID normalizado." : "CID não informado.") : "Use um CID como N39.0 ou Z00.");
  };

  const validateDate = () => {
    const feedback = document.getElementById("date-feedback");
    if (!dateInput.value) return setFeedback(dateInput, feedback, false, "Informe a data do documento.");
    const valid = dateInput.value <= dateInput.max;
    setFeedback(dateInput, feedback, valid, valid ? "Data válida." : "A data não pode estar no futuro.");
  };

  const updateDaysVisibility = () => {
    const isMedical = typeSelect.value === "atestado_medico";
    daysInput.required = isMedical;
    daysInput.closest("div").style.opacity = isMedical ? "1" : "0.55";
  };

  dateInput.max = new Date().toLocaleDateString("en-CA");
  cpfInput.addEventListener("input", validateCPF);
  cidInput.addEventListener("input", validateCID);
  cidInput.addEventListener("blur", validateCID);
  dateInput.addEventListener("change", validateDate);
  typeSelect.addEventListener("change", updateDaysVisibility);
  validateCPF();
  validateCID();
  validateDate();
  updateDaysVisibility();
})();
