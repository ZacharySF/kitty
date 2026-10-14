// Records anything that would submit an application, so tests can assert it never happened.
window.__submitted = 0;
window.__buttonClicks = 0;
window.__enterPresses = 0;
document.addEventListener('submit', e => { window.__submitted++; e.preventDefault(); }, true);
document.addEventListener('click', e => { if (e.target.closest('button')) window.__buttonClicks++; }, true);
document.addEventListener('keydown', e => { if (e.key === 'Enter') window.__enterPresses++; }, true);

// A react-select-like search box: typing (or the Down arrow) opens a role=listbox of role=option choices.
function searchBox(input, choices, {
  initialChoices = choices, delay = 0, commit = true,
  valueClass = 'select__single-value', rejectOnBlur = false, disabledChoices = [],
} = {}) {
  const control = input.closest('.select__control');
  const value = control.querySelector('.select__value');
  const list = document.createElement('div');
  list.id = input.id + '-listbox';
  list.setAttribute('role', 'listbox');
  control.appendChild(list);
  const searches = (window.__searches ??= {});
  searches[input.id] = [];
  let timer;
  const render = () => {
    const typed = input.value.toLowerCase();
    input.setAttribute('aria-controls', list.id);
    list.innerHTML = '';
    list.removeAttribute('aria-busy');
    const offered = typed ? choices.filter(c => c.toLowerCase().includes(typed)) : initialChoices;
    for (const choice of offered) {
      const option = document.createElement('div');
      option.setAttribute('role', 'option');
      option.textContent = choice;
      if (disabledChoices.includes(choice)) option.setAttribute('aria-disabled', 'true');
      option.addEventListener('mousedown', e => e.preventDefault());
      option.addEventListener('click', () => {
        if (!commit || disabledChoices.includes(choice)) return;
        if (commit === 'query') {
          input.value = choice; // Looks filled, but there is no committed selection.
        } else {
          value.innerHTML = '<span class="' + valueClass + '">' + choice + '</span>';
          input.value = '';
        }
        list.innerHTML = '';
        input.removeAttribute('aria-controls');
      });
      list.appendChild(option);
    }
  };
  input.addEventListener('input', () => {
    clearTimeout(timer);
    if (input.value) searches[input.id].push(input.value);
    if (delay && input.value) {
      input.setAttribute('aria-controls', list.id);
      list.innerHTML = '';
      list.setAttribute('aria-busy', 'true');
      timer = setTimeout(render, delay);
    } else {
      render();
    }
  });
  input.addEventListener('keydown', e => { if (e.key === 'ArrowDown') render(); });
  input.addEventListener('blur', () => {
    list.innerHTML = '';
    input.removeAttribute('aria-controls');
    if (rejectOnBlur) value.innerHTML = '';
  });
}
