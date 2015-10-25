var cursor = null;

function checked_rows() {
    return $('input[type="checkbox"]:checked').map(function (i, obj) {
        return $(obj).closest('tr');
    });

}

function on_check() {
    var rows = checked_rows();
    // Delete only when one or more selected
    if (rows.length > 0) {
        $('#del').removeAttr('disabled');
    } else {
        $('#del').attr('disabled', 'disabled');
    }

    // Edit only when one selected
    if (rows.length == 1) {
        $('#edit').removeAttr('disabled');
    } else {
        $('#edit').attr('disabled', 'disabled');
    }
}

function on_items(data) {
    cursor = data.cur;
    $.each(data.items, function(i, item) {
        var tr = $('<tr>');
        tr.data('item', item);

        var chk = $('<input type="checkbox" />');
        chk.click(on_check);
        tr.append($('<td></td>').append(chk));
        $.each(columns, function(i, col) {
            var td = $('<td/>');
            var val = $.isFunction(col.attr) ? col.attr(item) : item[col.attr];
            if (typeof(val) == 'string') {
                td.text(val);
            } else {
                td.append(val);
            }
            if ('class' in col) {
                td.addClass(col.class);
            }
            tr.append(td);
        });

        $('#items').append(tr);
    });

    $('#more').button('reset');
    if (!data.more) {
        $('#more').hide();
    }
}

function fetch_items() {
    $('#more').button('loading');
    var data = {};
    if (cursor) {
        data['cur'] = cursor;
    }

    $.ajax({
        url: url_base + obj_key,
        data: data,
        headers: {'Content-Type': 'application/json'},
        dataType: 'json',
        success: on_items
    });
}

function initialize_table(columns) {
    var tr = $('#header');
    tr.append('<th></th>');  // Checkbox
    $.map(columns, function(col) {
        tr.append('<th>' + col.title + '</th>');
    });
}

function dlg_fields() {
    return $('#dlg .form-control');
}

function dlg2item() {
    var item = {};
    dlg_fields().each(function(i, obj) {
        ctrl = $(obj);
        item[ctrl.attr('field')] = ctrl.val();
    });
    return item;
}

function on_add() {
    var item = dlg2item();
    $.ajax({
        url: url_post_base,
        dataType: 'json',
        type: 'POST',
        data: JSON.stringify(item),
        headers: {'Content-Type': 'application/json'},
        complete: function(jqXHR, textStatus) {
            // Reload when requests completes. Don't care if success or error.
            location.reload();
        }
    });
    // TODO: Do we really need to hide the dialog once we've reloaded?
    $('#dlg').modal('hide');

}

function open_add() {
    $('#dlg-label').text('Add ' + obj_type);
    $('#dlg-submit').text('Add')
    $('tr.user').show()
    dlg_fields().each(function(i, obj) {
        ctrl = $(obj);
        ctrl.val(ctrl.attr('default'));
    });
    $('#dlg-submit').click(on_add);
    $('#dlg').modal();
}

function confirm_delete() {
    var row_count = checked_rows().length;
    if (row_count > 0) {
        $('#confirm-body').text('Delete ' + row_count + ' items?');
        $('#del-confirm-btn').show();
    }
    else {
        $('#confirm-body').text('No rows selected!');
        $('#del-confirm-btn').hide();
    }
    $('#confirm').modal();
}

function on_delete() {
    var requests = []
    $.each(checked_rows(), function (i, row) {
        var key = row.data('item').key;
        requests.push($.ajax({
            url: url_base + key,
            dataType: 'json',
            type: 'DELETE'
        }));
        // TODO: Do we really need to hide the row now that we reload?
        row.hide();
    });
    // Reload when all requests complete (either successfully or in error, we don't care)
    $.when.apply(undefined, requests).then(function(a1, a2) {
        location.reload();
    })
    // TODO: Do we really need to hide the dialog once we've reloaded?
    $('#confirm').modal('hide');
}

function current_item() {
    return checked_rows()[0].data('item');
}

function on_edit() {
    var item = dlg2item();
    $.ajax({
        url: url_put_base + current_item().key,
        dataType: 'json',
        type: 'PUT',
        data: JSON.stringify(item),
        headers: {'Content-Type': 'application/json'},
        complete: function(jqXHR, textStatus) {
            // Reload when requests completes. Don't care if success or error.
            location.reload();
        }
    });
    $('#dlg').modal('hide');
}

function open_edit_item(item) {
    $('.modal-title#dlg-label').text('Edit ' + obj_type);
    $('#dlg-submit').text('Save')
    $('tr.user').hide()
    dlg_fields().each(function(i, obj) {
        ctrl = $(obj);
        ctrl.val(item[ctrl.attr('field') + '']);
    });
    $('#dlg-submit').click(on_edit);
    $('#dlg').modal();
}

function open_edit_selected() {
    var item = current_item();
    open_edit_item(item)
}

function on_ready() {
    initialize_table(columns);
    $('#add').click(open_add);
    $('#del').click(confirm_delete);
    $('#del-confirm-btn').click(on_delete);
    $('#edit').click(open_edit_selected);
    $('.datetimepicker').datetimepicker({ format: 'Y-m-d H:i' });
    fetch_items();
}

$(on_ready);
