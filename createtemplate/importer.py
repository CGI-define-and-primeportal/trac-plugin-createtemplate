import os
import subprocess
import shutil
# cElementTree is C implementation and faster
# http://eli.thegreenplace.net/2012/03/15/processing-xml-in-python-with-elementtree/
try:
    import xml.etree.cElementTree as ET
except ImportError:
    import xml.etree.ElementTree as ET

from trac.core import *
from trac.wiki.model import WikiPage
from trac.ticket.model import Type, Milestone
from trac.config import PathOption
from trac.util.datefmt import utc, parse_date

from logicaordertracker.controller import LogicaOrderController
from simplifiedpermissionsadminplugin.simplifiedpermissions import SimplifiedPermissions
from mailinglistplugin.model import Mailinglist

# Author: Danny Milsom <danny.milsom@cgi.com>

class ImportTemplate(Component):
    """Creates data and components inside #define based on XML template files"""

    template_dir_path = PathOption('project_templates', 'template_dir', '/var/define/templates',
                    doc="The default path for the project template directory")

    def import_wiki_pages(self, template_name):
        """Creates wiki pages inside the project using data extracted from
        an XML file. We don't set the author or version as that wouldn't 
        be applicable to a new project."""

        # open the wiki XML file, parse the data and create wiki pages
        full_path = os.path.join(self.template_dir_path, template_name, 'wiki.xml')
        tree = ET.ElementTree(file=full_path)
        for page in tree.getroot():
            wikipage = WikiPage(self.env, page.attrib['name'])
            wikipage.readonly = int(page.attrib['readonly']) # we store as a string in xml
            wikipage.text = page.text
            wikipage.save(None, None, None)
            self.log.info("Wiki page %s created" % page.attrib['name'])

    def import_wiki_attachments(self, template_name, project_name):
        """Imports wiki attachment files and inserts associated data into 
        the attachment wiki table"""

        # check that there are attachments to import
        template_attachment_path = os.path.join(self.template_dir_path, template_name, 'attachments', 'wiki')
        if os.path.isdir(template_attachment_path):

            # the directory we copy to can't exist before this
            project_attachment_path = os.path.join(self.env.path, 'attachments', 'wiki')
            if os.path.exists(project_attachment_path):
                shutil.rmtree(project_attachment_path)

            # copy the actual files (and create the attachment dir)
            shutil.copytree(template_attachment_path, project_attachment_path)
            self.log.info("Copied wiki attachments to %s", project_attachment_path)

            # insert meta-data into the wiki attachment table
            @self.env.with_transaction()
            def clear_and_insert_attachments(db):
                """Clears any wiki attachments from the current attachment table
                and inserts new rows based on attachment info from xml templates"""

                cursor = db.cursor()
                cursor.execute("DELETE FROM attachment WHERE type='wiki'")

                filepath = os.path.join(self.template_dir_path, template_name, 'attachment.xml')
                tree = ET.ElementTree(file=filepath)
                attachment_info = [('wiki', att.attrib['parent_id'], att.attrib['name'], 
                                    att.attrib['size'], att.text)
                                    for att in tree.getroot()]

                cursor.executemany("""INSERT INTO attachment(type, id, filename, size, description)
                                      VALUES (%s, %s, %s, %s, %s)""", attachment_info)

    def template_populate(self, template_name, project_name):
        """Clears tables of define default data and repopulates them with 
        template data taken from XML files.

        This function takes inspiration from define/env.py and the
        _clean_populate() method - although it allows us to only 
        delete certain records, not only full tables."""

        # for vales stored in the enum table we only want to clear certain rows
        enum_to_clear = list()

        # go through template dir to see which tables and rows we want to modify
        dir_path = os.path.join(self.template_dir_path, template_name)
        for filename in os.listdir(dir_path):
            if filename.lower().endswith("permission.xml"):
                self.import_perms(template_name)
            elif filename.lower().endswith("group.xml"):
                self.import_groups(template_name)
            elif filename.lower().endswith("milestone.xml"):
                self.import_milestones(template_name)
            elif filename.lower().endswith("priority.xml"):
                enum_to_clear.append("priority")
            elif filename.lower().endswith("ticket.xml"):
                enum_to_clear.append("ticket_type")

        if enum_to_clear:
            self.import_enum(template_name, enum_to_clear, project_name)

    def import_perms(self, template_name):
        """Parses the permissions XML file to get the data we need to insert
        into the permissions table. If we have this data we clear the existing
        permission data, and then insert the template data with an executemany()
        cursor method.

        If we don't create a perm_data list, we exit the function and 
        continue to use default data."""

        # parse the tree to get username, action data
        template_path = os.path.join(self.template_dir_path, template_name)
        for xml_file in os.listdir(template_path):
            if xml_file.endswith("permission.xml"):
                path = os.path.join(template_path, "permission.xml")
                tree = ET.ElementTree(file=path)
                perm_data = [(perm.attrib['name'], perm.attrib['action']) for perm in tree.getroot()]
            else:
                self.log.info("No permission data so using default data")
                return

        @self.env.with_transaction()
        def clear_and_insert_perms(db):
            """Clears the whole permissions table of default data, 
            and then inserts data from template."""

            cursor = db.cursor()
            self.log.info("Clearing permissions table")
            # cant pass the table name as an arg so its hard coded
            cursor.execute("DELETE FROM permission")

            self.log.info("Inserting template data into permissions table")
            cursor.executemany("""INSERT INTO permission(username, action)
                                  VALUES (%s, %s)""", perm_data)

    def import_groups(self, template_name):
        """Create project groups from templates after clearing the existing
        data in the groups table."""

        @self.env.with_transaction()
        def clear_perms(db):
            """Clears the whole groups table of default data. You can't pass
            a table name as an argument for parameter substitution, so it
            has to be hard coded."""
            cursor = db.cursor()
            self.log.info("Clearing permissions table")
            cursor.execute("DELETE FROM groups")

        self.log.info("Creating groups from template")
        path = os.path.join(self.template_dir_path, template_name, "group.xml")
        tree = ET.ElementTree(file=path)
        for group in tree.getroot():
            SimplifiedPermissions(self.env).add_group(group.attrib['name'], description=group.text)

    def import_milestones(self, template_name):
        """Deletes existing trac default milestones and creates new ones
        based on the information in milestone XML template"""

        @self.env.with_transaction()
        def clear_milestones(db):
            """Clears all rows in milestone table. This value is hard coded
            as you can't pass a table name with parameter substitution."""

            cursor = db.cursor()
            cursor.execute("""DELETE FROM milestone""")

        # Parse the XML tree and create milestones
        path = os.path.join(self.template_dir_path, template_name, "milestone.xml")
        tree = ET.ElementTree(file=path)
        for m in tree.getroot():
            milestone = Milestone(self.env)
            if 'name' in m.attrib:
                milestone.name = m.attrib['name']
            if 'start' in m.attrib:
                milestone.start = parse_date(m.attrib['start'])
            if 'due' in m.attrib:
                milestone.due = parse_date(m.attrib['due'])
            if 'completed' in m.attrib:
                milestone.completed = parse_date(m.attrib['completed'])
            if 'parent' in m.attrib:
                milestone.parent = m.attrib['parent']
            if m.text:
                milestone.description = m.text
            # save the milestone
            milestone.insert()

    def import_enum(self, template_name, types_to_remove, project_name):
        """Removes types from the enum table and then inserts data from the 
        template XML files."""

        # create a list of tuples for every enum type in our template 
        # where the tuple follows the synax (type, name, value)
        template_path = os.path.join(self.template_dir_path, template_name)
        for xml_file in os.listdir(template_path):
            if xml_file.endswith("priority.xml"):
                path = os.path.join(template_path, 'priority.xml')
                tree = ET.ElementTree(file=path)
                priority_list = [('priority', priority.attrib['name'], priority.attrib['value']) for priority in tree.getroot()]

        values = list()
        values.extend(priority_list)

        @self.env.with_transaction()
        def clear_and_insert_enum(db):
            """Clears enum table rows where data type is replicated in our 
            template, such as priorty and timeaccount."""

            cursor = db.cursor()
            self.log.info("Clearing enum table")
            cursor.execute("""DELETE FROM enum
                              WHERE type IN ({0})
                              """.format(','.join(('%s',)*len(types_to_remove))),
                              types_to_remove)

            self.log.info("Inserting template data into enum table")
            cursor.executemany("""INSERT INTO enum (type, name, value) 
                                  VALUES (%s, %s, %s)
                                  """, values)

        # now import ticket types from template
        # we use LogicaOrderController rather than a straight SQL insert
        self.import_ticket_types(template_path) 
        self.import_workflows(template_path)

    def import_ticket_types(self, template_path):
        """Create ticket types using the import functionality from 
        LogicaOrderController and data from a ticket type template 
        XML."""

        # get ticket info in JSON format from XML file
        controller = LogicaOrderController(self.env)

        self.log.info("Creating ticket types from template")
        tree = ET.ElementTree(file=os.path.join(template_path, 'ticket.xml'))
        for ticket in tree.getroot():
            # using a _method() is a bit naughty
            controller._import_ticket_type(ticket.text, dry_run=False)

    def import_workflows(self, template_name):
        """Copies all workflow files from the template directory to our new 
        project's workflow directory."""

        template_workflow_path = os.path.join(self.template_dir_path, template_name, 'workflows')
        project_workflow_path = os.path.join(self.env.path, 'workflows')

        # the directory we copy to can't exist if using shutil.copytree
        # but it is created in manage_project()
        if os.path.exists(project_workflow_path):
            shutil.rmtree(project_workflow_path)
        shutil.copytree(template_workflow_path, project_workflow_path)
        self.log.info("Copied ticket workflows to %s", project_workflow_path)

    def import_mailinglist(self, template_name):
        """Create new mailing lists based on the mailng list XML 
        template."""

        path = os.path.join(self.template_dir_path, template_name, 'list.xml')
        tree = ET.ElementTree(file=path)
        for ml in tree.getroot():
            mailinglist = Mailinglist(self.env, emailaddress=ml.attrib['email'],
                                           name=ml.attrib['name'],
                                           description=ml.text,
                                           private=ml.attrib['private'],
                                           postperm=ml.attrib['postperm'],
                                           replyto=ml.attrib['replyto'])
            mailinglist.insert()

        # TODO Get Subscriber informaiton 
        # mailinglist.subscribe(group='project_group', poser=True)

    def import_file_archive(self, template_name, project_name):
        """Create a new subversion repository using the dump file in 
        the template directory."""

        old_repo_path = os.path.join(self.template_dir_path, template_name,  template_name + '.dump.gz')
        new_repo_path = os.path.join('vc-repos', 'svn', project_name)

        if os.path.exists(old_repo_path) and os.path.exists(new_repo_path):
            subprocess.call("zcat %s | svnadmin load %s" % (old_repo_path, new_repo_path), cwd=os.getcwd(), shell=True)
            self.log.info("Imported Subversion file archive from %s" % old_repo_path)